"""
任务调度器 - 管理发送任务队列和频率限制
"""
import time
import logging
import threading
from datetime import datetime, timezone, timedelta

from config import SEND_COOLDOWN_SECONDS, DAILY_SEND_LIMIT
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

        # 检查每日发送上限
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = session.query(SendTask).filter(
            SendTask.account_id == account_id,
            SendTask.status == 'completed',
            SendTask.completed_at >= today_start
        ).count()
        if daily_count >= DAILY_SEND_LIMIT:
            logger.debug(f"[{account.name}] 今日已完成 {daily_count} 个任务，达到上限 {DAILY_SEND_LIMIT}，跳过")
            return None

        # 检查24小时消息限制
        if account.rate_limited_until:
            rate_limited_until = account.rate_limited_until
            if rate_limited_until.tzinfo is None:
                rate_limited_until = rate_limited_until.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < rate_limited_until:
                remaining_h = (rate_limited_until - datetime.now(timezone.utc)).total_seconds() / 3600
                logger.debug(f"[{account.name}] 24小时消息限制中，还需等待 {remaining_h:.1f}h")
                return None
            else:
                # 限制已过期，清除标记
                account.rate_limited_until = None
                account.status = 'active'
                session.commit()
                logger.info(f"[{account.name}] 24小时消息限制已解除")

        # 检查冷却时间
        if account.last_task_at:
            last_task_at = account.last_task_at
            if last_task_at.tzinfo is None:
                last_task_at = last_task_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_task_at).total_seconds()
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
            # 检测24小时消息限制标记
            if not success and 'RATE_LIMITED' in (detail or ''):
                account.rate_limited_until = datetime.now(timezone.utc) + timedelta(hours=24)
                account.status = 'rate_limited'
                logger.warning(f"[{account_name}] 触发24小时消息限制，暂停至 {account.rate_limited_until}")
                # 将该账号的所有pending DM任务标记为skipped
                pending_dm_tasks = session.query(SendTask).filter(
                    SendTask.account_id == account_id,
                    SendTask.task_type == 'dm',
                    SendTask.status == 'pending'
                ).all()
                for t in pending_dm_tasks:
                    t.status = 'skipped'
                    t.error_message = '24小时消息限制'
                    t.completed_at = datetime.now(timezone.utc)
                logger.info(f"[{account_name}] 已跳过 {len(pending_dm_tasks)} 个待发DM任务")

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


def _run_sender_for_account(account_id, account_name, cookie_url):
    """单个发送账号的任务处理循环（独立线程+独立浏览器）"""
    logger.info(f"[{account_name}] 发送线程启动，初始化浏览器...")

    _sender_status[account_id] = {
        "account_name": account_name,
        "current_task": None,
        "status": "initializing",
        "last_update": datetime.now(timezone.utc).isoformat(),
    }

    # 创建该账号专属的发送引擎
    engine = SenderEngine(account_name, cookie_url)
    if not engine.initialize():
        logger.error(f"[{account_name}] 发送引擎初始化失败")
        _sender_status[account_id] = {
            "account_name": account_name,
            "current_task": None,
            "status": "engine_failed",
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        return

    _sender_engines[account_id] = engine
    logger.info(f"[{account_name}] 浏览器初始化成功，开始处理任务...")

    _sender_status[account_id] = {
        "account_name": account_name,
        "current_task": None,
        "status": "idle",
        "last_update": datetime.now(timezone.utc).isoformat(),
    }

    while _task_processor_running:
        try:
            # 获取下一个任务
            task_info = get_next_task(account_id)
            if not task_info:
                time.sleep(30)
                continue

            # 更新状态
            _sender_status[account_id] = {
                "account_name": account_name,
                "current_task": task_info["task_type"],
                "status": "executing",
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

            # 执行任务
            execute_task(task_info, engine)

            # 更新状态
            _sender_status[account_id] = {
                "account_name": account_name,
                "current_task": None,
                "status": "idle",
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

            time.sleep(5)

        except Exception as e:
            logger.error(f"[{account_name}] 任务处理出错: {e}")
            time.sleep(10)

    # 清理该账号的发送引擎
    engine.cleanup()
    _sender_engines.pop(account_id, None)
    logger.info(f"[{account_name}] 发送线程已停止")


# 跟踪每个账号的发送线程
_sender_threads = {}


def run_task_processor():
    """发送任务处理主循环 - 为每个sender账号启动独立线程"""
    global _task_processor_running
    _task_processor_running = True

    logger.info("发送任务处理器已启动")

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
        logger.warning("没有可用的发送账号")
        return

    logger.info(f"发现 {len(accounts_info)} 个发送账号，逐个启动独立线程...")

    # 为每个账号启动独立线程
    for account_id, account_name, cookie_url in accounts_info:
        t = threading.Thread(
            target=_run_sender_for_account,
            args=(account_id, account_name, cookie_url),
            daemon=True,
        )
        _sender_threads[account_id] = t
        t.start()
        logger.info(f"[{account_name}] 发送线程已启动")
        time.sleep(5)  # 错开启动时间，避免同时创建浏览器

    # 主循环等待所有线程
    try:
        while _task_processor_running:
            time.sleep(5)
    except KeyboardInterrupt:
        _task_processor_running = False

    # 等待所有线程结束
    logger.info("正在停止所有发送线程...")
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
