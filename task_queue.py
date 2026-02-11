"""
任务调度器 - 管理发送任务队列和频率限制
"""
import time
import logging
import threading
from datetime import datetime, timezone

from config import SEND_COOLDOWN_SECONDS
from models import get_session, Account, SendTask, PostAction, WhatsAppAccount, Post
from ai_analyzer import generate_comment, generate_dm_with_whatsapp, generate_dm_without_whatsapp
from sender import SenderEngine

logger = logging.getLogger(__name__)

# 全局状态
_task_processor_running = False
_task_processor_thread = None
_sender_engines = {}  # account_id -> SenderEngine
_sender_status = {}  # account_id -> {状态信息}


def generate_tasks_for_post(post_data):
    """为目标帖子生成发送任务（全覆盖：每个sender账号都要触达）"""
    session = get_session()
    try:
        # 获取帖子的数据库ID
        post_id = post_data.get("id")
        if not post_id:
            # 通过post_id字符串查找
            post = session.query(Post).filter(Post.post_id == post_data.get("post_id")).first()
            if post:
                post_id = post.id
            else:
                logger.warning(f"找不到帖子: {post_data.get('post_id')}")
                return

        # 获取所有已启用的sender账号
        sender_accounts = session.query(Account).filter(
            Account.account_type == 'sender',
            Account.enabled == True
        ).all()

        if not sender_accounts:
            logger.info("没有可用的发送账号，跳过任务生成")
            return

        tasks_created = 0
        for account in sender_accounts:
            # 检查是否已有该帖子+账号的任务（避免重复）
            existing = session.query(SendTask).filter(
                SendTask.post_id == post_id,
                SendTask.account_id == account.id
            ).first()
            if existing:
                continue

            # 为每个账号创建3个任务：评论、私信、加好友
            for task_type in ['comment', 'dm', 'add_friend']:
                task = SendTask(
                    post_id=post_id,
                    account_id=account.id,
                    task_type=task_type,
                    status='pending',
                )
                session.add(task)
                tasks_created += 1

        session.commit()
        if tasks_created > 0:
            logger.info(f"已为帖子 {post_id} 生成 {tasks_created} 个发送任务")
    except Exception as e:
        session.rollback()
        logger.error(f"生成发送任务失败: {e}")
    finally:
        session.close()


def get_next_task(account_id):
    """获取指定账号的下一个待执行任务（遵守频率限制）"""
    session = get_session()
    try:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return None

        # 检查冷却时间
        if account.last_task_at:
            elapsed = (datetime.now(timezone.utc) - account.last_task_at).total_seconds()
            if elapsed < SEND_COOLDOWN_SECONDS:
                remaining = SEND_COOLDOWN_SECONDS - elapsed
                logger.debug(f"[{account.name}] 冷却中，还需等待 {remaining:.0f}s")
                return None

        # 获取最早的pending任务
        task = session.query(SendTask).filter(
            SendTask.account_id == account_id,
            SendTask.status == 'pending'
        ).order_by(SendTask.created_at.asc()).first()

        if task:
            return {
                "id": task.id,
                "post_id": task.post_id,
                "account_id": task.account_id,
                "task_type": task.task_type,
                "account_name": account.name,
                "cookie_url": account.cookie_url,
                "whatsapp_account_id": account.whatsapp_account_id,
            }
        return None
    finally:
        session.close()


def execute_task(task_info, sender_engine):
    """执行单个发送任务"""
    task_id = task_info["id"]
    task_type = task_info["task_type"]
    post_id = task_info["post_id"]
    account_id = task_info["account_id"]
    account_name = task_info["account_name"]

    session = get_session()
    try:
        # 标记为进行中
        task = session.query(SendTask).filter(SendTask.id == task_id).first()
        if not task:
            return
        task.status = 'in_progress'
        task.started_at = datetime.now(timezone.utc)
        session.commit()

        # 获取帖子信息
        post = session.query(Post).filter(Post.id == post_id).first()
        if not post:
            task.status = 'failed'
            task.error_message = '帖子不存在'
            task.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        success = False
        detail = ""
        generated_text = ""

        if task_type == 'comment':
            # 生成评论内容
            generated_text = generate_comment(post.content or "")
            if not generated_text:
                task.status = 'failed'
                task.error_message = 'AI生成评论失败'
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

            post_url = post.post_url
            if not post_url and post.post_id:
                post_url = f"https://www.facebook.com/{post.post_id}"

            if not post_url:
                task.status = 'failed'
                task.error_message = '无帖子URL'
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

            success, detail = sender_engine.execute_comment(post_url, generated_text)

        elif task_type == 'dm':
            # 检查WhatsApp配对
            whatsapp_id = task_info.get("whatsapp_account_id")
            if whatsapp_id:
                wa = session.query(WhatsAppAccount).filter(
                    WhatsAppAccount.id == whatsapp_id,
                    WhatsAppAccount.enabled == True
                ).first()
                if wa:
                    generated_text = generate_dm_with_whatsapp(post.content or "", wa.phone_number)
                    # 更新使用次数
                    wa.usage_count = (wa.usage_count or 0) + 1
                    session.commit()
                else:
                    generated_text = generate_dm_without_whatsapp(post.content or "")
            else:
                generated_text = generate_dm_without_whatsapp(post.content or "")

            if not generated_text:
                task.status = 'failed'
                task.error_message = 'AI生成私信失败'
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

            author_id = post.author_id
            if not author_id:
                task.status = 'failed'
                task.error_message = '无用户ID'
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

            success, detail = sender_engine.execute_dm(author_id, generated_text)

        elif task_type == 'add_friend':
            author_id = post.author_id
            if not author_id:
                task.status = 'failed'
                task.error_message = '无用户ID'
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

            success, detail = sender_engine.execute_add_friend(author_id)

        # 更新任务状态
        task.status = 'completed' if success else 'failed'
        task.error_message = detail if not success else None
        task.generated_text = generated_text
        task.completed_at = datetime.now(timezone.utc)

        # 更新账号最后操作时间
        account = session.query(Account).filter(Account.id == account_id).first()
        if account:
            account.last_task_at = datetime.now(timezone.utc)
            if not success and '被限制' in (detail or ''):
                account.status = 'banned'

        # 同步写入post_actions表
        action = PostAction(
            post_id=post_id,
            account_id=account_name,
            action_type=task_type if task_type != 'dm' else 'message',
            action_status='success' if success else 'failed',
            action_detail=detail,
            send_task_id=task_id,
        )
        session.add(action)
        session.commit()

        logger.info(f"[{account_name}] 任务 {task_id} ({task_type}) {'成功' if success else '失败'}: {detail}")

    except Exception as e:
        session.rollback()
        logger.error(f"执行任务 {task_id} 出错: {e}")
        try:
            task = session.query(SendTask).filter(SendTask.id == task_id).first()
            if task:
                task.status = 'failed'
                task.error_message = str(e)
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            session.rollback()
    finally:
        session.close()


def _get_or_create_sender_engine(account_id, account_name, cookie_url):
    """获取或创建发送引擎实例"""
    if account_id in _sender_engines:
        engine = _sender_engines[account_id]
        if engine.initialized:
            return engine

    engine = SenderEngine(account_name, cookie_url)
    if engine.initialize():
        _sender_engines[account_id] = engine
        return engine
    return None


def run_task_processor():
    """发送任务处理主循环"""
    global _task_processor_running
    _task_processor_running = True

    logger.info("发送任务处理器已启动")

    while _task_processor_running:
        try:
            # 获取所有已启用的sender账号
            session = get_session()
            try:
                sender_accounts = session.query(Account).filter(
                    Account.account_type == 'sender',
                    Account.enabled == True,
                    Account.status != 'banned'
                ).all()
                accounts_info = [(a.id, a.name, a.cookie_url) for a in sender_accounts if a.cookie_url]
            finally:
                session.close()

            if not accounts_info:
                time.sleep(30)
                continue

            task_executed = False
            for account_id, account_name, cookie_url in accounts_info:
                if not _task_processor_running:
                    break

                # 获取下一个任务
                task_info = get_next_task(account_id)
                if not task_info:
                    continue

                # 更新状态
                _sender_status[account_id] = {
                    "account_name": account_name,
                    "current_task": task_info["task_type"],
                    "status": "executing",
                    "last_update": datetime.now(timezone.utc).isoformat(),
                }

                # 获取或创建发送引擎
                engine = _get_or_create_sender_engine(account_id, account_name, cookie_url)
                if not engine:
                    logger.error(f"[{account_name}] 发送引擎初始化失败")
                    _sender_status[account_id] = {
                        "account_name": account_name,
                        "current_task": None,
                        "status": "engine_failed",
                        "last_update": datetime.now(timezone.utc).isoformat(),
                    }
                    continue

                # 执行任务
                execute_task(task_info, engine)
                task_executed = True

                # 更新状态
                _sender_status[account_id] = {
                    "account_name": account_name,
                    "current_task": None,
                    "status": "idle",
                    "last_update": datetime.now(timezone.utc).isoformat(),
                }

            # 如果没有执行任何任务，等待更长时间
            if not task_executed:
                time.sleep(30)
            else:
                time.sleep(5)

        except Exception as e:
            logger.error(f"任务处理循环出错: {e}")
            time.sleep(10)

    # 清理所有发送引擎
    for engine in _sender_engines.values():
        engine.cleanup()
    _sender_engines.clear()

    logger.info("发送任务处理器已停止")


def start_task_processor():
    """启动任务处理器线程"""
    global _task_processor_thread, _task_processor_running

    if _task_processor_running and _task_processor_thread and _task_processor_thread.is_alive():
        logger.warning("任务处理器已在运行中")
        return

    _task_processor_thread = threading.Thread(target=run_task_processor, daemon=True)
    _task_processor_thread.start()
    logger.info("任务处理器线程已启动")


def stop_task_processor():
    """停止任务处理器"""
    global _task_processor_running
    _task_processor_running = False
    logger.info("已发送停止信号给任务处理器")


def get_all_sender_status():
    """获取所有发送账号的状态"""
    return {
        "running": _task_processor_running,
        "accounts": _sender_status,
    }
