import logging
import os
import requests as http_requests
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from sqlalchemy import func

from config import FLASK_PORT, COOKIES_DIR
from models import (
    init_db, get_session, Post, PostAction, MonitorLog,
    Account, WhatsAppAccount, SendTask
)
from monitor import start_monitor_thread, stop_monitor, monitor_status

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'fb_monitor_secret_2024'
# 避免Jinja2与Vue.js模板语法冲突
app.jinja_env.variable_start_string = '[['
app.jinja_env.variable_end_string = ']]'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 发送状态（全局）
sending_status = {
    "running": False,
    "accounts": {}
}


# ============ 页面路由 ============
@app.route('/')
def index():
    return render_template('index.html')


# ============ 监控 API ============
@app.route('/api/status')
def get_status():
    """获取监控状态"""
    return jsonify(monitor_status)


@app.route('/api/start', methods=['POST'])
def start_monitoring():
    """启动监控"""
    if monitor_status.get("running"):
        return jsonify({"success": False, "message": "监控已在运行中"})

    success = start_monitor_thread()
    return jsonify({"success": success, "message": "监控已启动" if success else "启动失败"})


@app.route('/api/stop', methods=['POST'])
def stop_monitoring():
    """停止监控"""
    stop_monitor()
    return jsonify({"success": True, "message": "已发送停止信号"})


# ============ 帖子 API ============
@app.route('/api/posts')
def get_posts():
    """获取帖子列表"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    source = request.args.get('source', '')
    is_target = request.args.get('is_target', '')
    search = request.args.get('search', '')

    session = get_session()
    try:
        query = session.query(Post).order_by(Post.created_at.desc())

        if source:
            query = query.filter(Post.source_page == source)
        if is_target == 'true':
            query = query.filter(Post.is_target == True)
        elif is_target == 'false':
            query = query.filter(Post.is_target == False)
        if search:
            query = query.filter(Post.content.ilike(f'%{search}%'))

        total = query.count()
        posts = query.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            "success": True,
            "data": [p.to_dict() for p in posts],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        })
    finally:
        session.close()


@app.route('/api/posts/<int:post_db_id>')
def get_post_detail(post_db_id):
    """获取帖子详情"""
    session = get_session()
    try:
        post = session.query(Post).filter(Post.id == post_db_id).first()
        if not post:
            return jsonify({"success": False, "message": "帖子不存在"}), 404
        data = post.to_dict()
        # 附加发送任务信息
        tasks = session.query(SendTask).filter(SendTask.post_id == post_db_id).all()
        data["send_tasks"] = [t.to_dict() for t in tasks]
        return jsonify({"success": True, "data": data})
    finally:
        session.close()


@app.route('/api/posts/<int:post_db_id>/actions')
def get_post_actions(post_db_id):
    """获取帖子的操作记录"""
    session = get_session()
    try:
        actions = session.query(PostAction).filter(
            PostAction.post_id == post_db_id
        ).order_by(PostAction.created_at.desc()).all()
        return jsonify({
            "success": True,
            "data": [a.to_dict() for a in actions],
        })
    finally:
        session.close()


@app.route('/api/stats')
def get_stats():
    """获取统计数据"""
    session = get_session()
    try:
        total_posts = session.query(Post).count()
        target_posts = session.query(Post).filter(Post.is_target == True).count()
        liked_posts = session.query(Post).filter(Post.action_liked == True).count()
        interested_posts = session.query(Post).filter(Post.action_interested == True).count()

        # 各来源统计
        source_stats = session.query(
            Post.source_page, func.count(Post.id)
        ).group_by(Post.source_page).all()

        # 最近日志
        recent_logs = session.query(MonitorLog).order_by(
            MonitorLog.id.desc()
        ).limit(10).all()

        return jsonify({
            "success": True,
            "data": {
                "total_posts": total_posts,
                "target_posts": target_posts,
                "non_target_posts": total_posts - target_posts,
                "liked_posts": liked_posts,
                "interested_posts": interested_posts,
                "source_stats": {s[0]: s[1] for s in source_stats},
                "recent_logs": [l.to_dict() for l in recent_logs],
            }
        })
    finally:
        session.close()


@app.route('/api/logs')
def get_logs():
    """获取监控日志"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    session = get_session()
    try:
        query = session.query(MonitorLog).order_by(MonitorLog.id.desc())
        total = query.count()
        logs = query.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            "success": True,
            "data": [l.to_dict() for l in logs],
            "total": total,
        })
    finally:
        session.close()


# ============ 账号管理 API ============
@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """获取账号列表"""
    account_type = request.args.get('type', '')
    session = get_session()
    try:
        query = session.query(Account)
        if account_type:
            query = query.filter(Account.account_type == account_type)
        accounts = query.order_by(Account.created_at.desc()).all()
        return jsonify({"success": True, "data": [a.to_dict() for a in accounts]})
    finally:
        session.close()


@app.route('/api/accounts', methods=['POST'])
def create_account():
    """创建账号"""
    data = request.get_json()
    if not data or not data.get('name') or not data.get('account_type'):
        return jsonify({"success": False, "message": "缺少必要参数 name 和 account_type"}), 400
    if data['account_type'] not in ('monitor', 'sender'):
        return jsonify({"success": False, "message": "account_type 必须是 monitor 或 sender"}), 400

    session = get_session()
    try:
        existing = session.query(Account).filter(Account.name == data['name']).first()
        if existing:
            return jsonify({"success": False, "message": "账号名称已存在"}), 400

        account = Account(
            name=data['name'],
            account_type=data['account_type'],
            cookie_url=data.get('cookie_url', ''),
            enabled=data.get('enabled', True),
            whatsapp_account_id=data.get('whatsapp_account_id'),
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        return jsonify({"success": True, "data": account.to_dict(), "message": "账号创建成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"创建失败: {e}"}), 500
    finally:
        session.close()


@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
def update_account(account_id):
    """更新账号"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "缺少更新数据"}), 400

    session = get_session()
    try:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return jsonify({"success": False, "message": "账号不存在"}), 404

        for field in ['name', 'cookie_url', 'cookie_status', 'status', 'enabled', 'whatsapp_account_id']:
            if field in data:
                setattr(account, field, data[field])
        session.commit()
        session.refresh(account)
        return jsonify({"success": True, "data": account.to_dict(), "message": "更新成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"更新失败: {e}"}), 500
    finally:
        session.close()


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """删除账号"""
    session = get_session()
    try:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return jsonify({"success": False, "message": "账号不存在"}), 404
        session.delete(account)
        session.commit()
        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"删除失败: {e}"}), 500
    finally:
        session.close()


@app.route('/api/accounts/<int:account_id>/refresh-cookie', methods=['POST'])
def refresh_account_cookie(account_id):
    """重新下载账号Cookie"""
    session = get_session()
    try:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return jsonify({"success": False, "message": "账号不存在"}), 404
        if not account.cookie_url:
            return jsonify({"success": False, "message": "该账号没有设置Cookie URL"}), 400

        success, msg = _download_cookie_from_url(account.cookie_url, account.name)
        if success:
            account.cookie_status = 'valid'
            session.commit()
            return jsonify({"success": True, "message": "Cookie已刷新"})
        else:
            account.cookie_status = 'invalid'
            session.commit()
            return jsonify({"success": False, "message": f"Cookie下载失败: {msg}"}), 500
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"刷新失败: {e}"}), 500
    finally:
        session.close()


# ============ Cookie上传 API ============
@app.route('/api/cookies/upload', methods=['POST'])
def upload_cookie():
    """上传Cookie URL"""
    data = request.get_json()
    if not data or not data.get('cookie_url'):
        return jsonify({"success": False, "message": "缺少 cookie_url 参数"}), 400
    if not data.get('account_type') or data['account_type'] not in ('monitor', 'sender'):
        return jsonify({"success": False, "message": "account_type 必须是 monitor 或 sender"}), 400

    cookie_url = data['cookie_url']
    account_name = data.get('account_name', f"{data['account_type']}_{datetime.now().strftime('%Y%m%d%H%M%S')}")

    # 下载并验证Cookie
    success, msg = _download_cookie_from_url(cookie_url, account_name)
    if not success:
        return jsonify({"success": False, "message": f"Cookie下载失败: {msg}"}), 400

    # 保存或更新账号
    session = get_session()
    try:
        account = session.query(Account).filter(Account.name == account_name).first()
        if account:
            account.cookie_url = cookie_url
            account.cookie_status = 'valid'
        else:
            account = Account(
                name=account_name,
                account_type=data['account_type'],
                cookie_url=cookie_url,
                cookie_status='valid',
                enabled=True,
            )
            session.add(account)
        session.commit()
        session.refresh(account)
        return jsonify({"success": True, "data": account.to_dict(), "message": "Cookie上传成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"保存失败: {e}"}), 500
    finally:
        session.close()


def _download_cookie_from_url(cookie_url, account_name):
    """下载Cookie文件并保存到本地"""
    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)
        resp = http_requests.get(cookie_url, timeout=30, proxies={'http': None, 'https': None})
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"

        # 验证JSON格式
        import json
        try:
            json.loads(resp.text)
        except json.JSONDecodeError:
            return False, "无效的JSON格式"

        file_path = os.path.join(COOKIES_DIR, f"{account_name}_cookies.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(resp.text)

        return True, file_path
    except Exception as e:
        return False, str(e)


# ============ WhatsApp账号 API ============
@app.route('/api/whatsapp-accounts', methods=['GET'])
def list_whatsapp_accounts():
    """获取WhatsApp账号列表"""
    session = get_session()
    try:
        accounts = session.query(WhatsAppAccount).order_by(WhatsAppAccount.created_at.desc()).all()
        return jsonify({"success": True, "data": [a.to_dict() for a in accounts]})
    finally:
        session.close()


@app.route('/api/whatsapp-accounts', methods=['POST'])
def create_whatsapp_account():
    """创建WhatsApp账号"""
    data = request.get_json()
    if not data or not data.get('phone_number'):
        return jsonify({"success": False, "message": "缺少 phone_number 参数"}), 400

    session = get_session()
    try:
        existing = session.query(WhatsAppAccount).filter(
            WhatsAppAccount.phone_number == data['phone_number']
        ).first()
        if existing:
            return jsonify({"success": False, "message": "该WhatsApp号码已存在"}), 400

        wa = WhatsAppAccount(
            phone_number=data['phone_number'],
            enabled=data.get('enabled', True),
        )
        session.add(wa)
        session.commit()
        session.refresh(wa)
        return jsonify({"success": True, "data": wa.to_dict(), "message": "WhatsApp账号创建成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"创建失败: {e}"}), 500
    finally:
        session.close()


@app.route('/api/whatsapp-accounts/<int:wa_id>', methods=['PUT'])
def update_whatsapp_account(wa_id):
    """更新WhatsApp账号"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "缺少更新数据"}), 400

    session = get_session()
    try:
        wa = session.query(WhatsAppAccount).filter(WhatsAppAccount.id == wa_id).first()
        if not wa:
            return jsonify({"success": False, "message": "WhatsApp账号不存在"}), 404

        for field in ['phone_number', 'enabled']:
            if field in data:
                setattr(wa, field, data[field])
        session.commit()
        session.refresh(wa)
        return jsonify({"success": True, "data": wa.to_dict(), "message": "更新成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"更新失败: {e}"}), 500
    finally:
        session.close()


@app.route('/api/whatsapp-accounts/<int:wa_id>', methods=['DELETE'])
def delete_whatsapp_account(wa_id):
    """删除WhatsApp账号"""
    session = get_session()
    try:
        wa = session.query(WhatsAppAccount).filter(WhatsAppAccount.id == wa_id).first()
        if not wa:
            return jsonify({"success": False, "message": "WhatsApp账号不存在"}), 404

        # 检查是否被sender关联
        linked = session.query(Account).filter(Account.whatsapp_account_id == wa_id).count()
        if linked > 0:
            return jsonify({"success": False, "message": f"该WhatsApp号码被 {linked} 个发送账号关联，请先解除关联"}), 400

        session.delete(wa)
        session.commit()
        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "message": f"删除失败: {e}"}), 500
    finally:
        session.close()


# ============ 发送控制 API ============
@app.route('/api/sending/start', methods=['POST'])
def start_sending():
    """启动发送任务处理器"""
    if sending_status["running"]:
        return jsonify({"success": False, "message": "发送已在运行中"})

    try:
        from task_queue import start_task_processor
        start_task_processor()
        sending_status["running"] = True
        return jsonify({"success": True, "message": "发送已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": f"启动失败: {e}"}), 500


@app.route('/api/sending/stop', methods=['POST'])
def stop_sending():
    """停止发送"""
    try:
        from task_queue import stop_task_processor
        stop_task_processor()
        sending_status["running"] = False
        return jsonify({"success": True, "message": "已发送停止信号"})
    except Exception as e:
        return jsonify({"success": False, "message": f"停止失败: {e}"}), 500


@app.route('/api/sending/status')
def get_sending_status():
    """获取发送状态"""
    try:
        from task_queue import get_all_sender_status
        status = get_all_sender_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": True, "data": {"running": False, "accounts": {}}})


@app.route('/api/sending/tasks')
def get_sending_tasks():
    """获取发送任务详情列表（含帖子链接、用户主页、生成内容）"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    status_filter = request.args.get('status', '')
    task_type = request.args.get('task_type', '')
    account_id = request.args.get('account_id', '', type=str)

    session = get_session()
    try:
        query = session.query(SendTask).order_by(SendTask.created_at.desc())
        if status_filter:
            query = query.filter(SendTask.status == status_filter)
        if task_type:
            query = query.filter(SendTask.task_type == task_type)
        if account_id:
            query = query.filter(SendTask.account_id == int(account_id))

        total = query.count()
        tasks = query.offset((page - 1) * per_page).limit(per_page).all()

        result = []
        for t in tasks:
            task_dict = t.to_dict()
            # 附加帖子信息
            if t.post:
                task_dict["post_url"] = t.post.post_url
                task_dict["post_content"] = (t.post.content or "")[:200]
                task_dict["author_name"] = t.post.author_name
                task_dict["author_id"] = t.post.author_id
                task_dict["author_profile_url"] = t.post.author_profile_url
            result.append(task_dict)

        return jsonify({
            "success": True,
            "data": result,
            "total": total,
            "page": page,
            "per_page": per_page,
        })
    finally:
        session.close()


# ============ 统计面板 API ============
@app.route('/api/stats/dashboard')
def get_dashboard_stats():
    """获取统计面板数据"""
    session = get_session()
    try:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # 发送账号统计
        sender_accounts = session.query(Account).filter(Account.account_type == 'sender').all()
        sender_stats = []
        for sa in sender_accounts:
            # 总评论数
            total_comments = session.query(SendTask).filter(
                SendTask.account_id == sa.id,
                SendTask.task_type == 'comment',
                SendTask.status == 'completed'
            ).count()
            # 总私信数
            total_dms = session.query(SendTask).filter(
                SendTask.account_id == sa.id,
                SendTask.task_type == 'dm',
                SendTask.status == 'completed'
            ).count()
            # 今日私信数
            daily_dms = session.query(SendTask).filter(
                SendTask.account_id == sa.id,
                SendTask.task_type == 'dm',
                SendTask.status == 'completed',
                SendTask.completed_at >= today_start
            ).count()
            # 今日加好友数
            daily_friends = session.query(SendTask).filter(
                SendTask.account_id == sa.id,
                SendTask.task_type == 'add_friend',
                SendTask.status == 'completed',
                SendTask.completed_at >= today_start
            ).count()
            # 最近操作
            recent_tasks = session.query(SendTask).filter(
                SendTask.account_id == sa.id,
                SendTask.status == 'completed'
            ).order_by(SendTask.completed_at.desc()).limit(10).all()

            sender_stats.append({
                "id": sa.id,
                "name": sa.name,
                "status": sa.status,
                "enabled": sa.enabled,
                "whatsapp_phone": sa.whatsapp_account.phone_number if sa.whatsapp_account else None,
                "total_comments": total_comments,
                "total_dms": total_dms,
                "daily_dms": daily_dms,
                "daily_friend_requests": daily_friends,
                "recent_actions": [t.to_dict() for t in recent_tasks],
            })

        # 监控账号统计
        monitor_accounts = session.query(Account).filter(Account.account_type == 'monitor').all()
        monitor_stats = []
        for ma in monitor_accounts:
            target_count = session.query(Post).filter(
                Post.discovered_by == ma.name,
                Post.is_target == True
            ).count()
            total_count = session.query(Post).filter(Post.discovered_by == ma.name).count()
            monitor_stats.append({
                "id": ma.id,
                "name": ma.name,
                "enabled": ma.enabled,
                "target_posts_found": target_count,
                "total_posts_found": total_count,
            })

        # WhatsApp统计
        wa_accounts = session.query(WhatsAppAccount).all()
        wa_stats = [a.to_dict() for a in wa_accounts]

        # 任务队列统计
        pending_tasks = session.query(SendTask).filter(SendTask.status == 'pending').count()
        in_progress_tasks = session.query(SendTask).filter(SendTask.status == 'in_progress').count()
        completed_today = session.query(SendTask).filter(
            SendTask.status == 'completed',
            SendTask.completed_at >= today_start
        ).count()
        failed_today = session.query(SendTask).filter(
            SendTask.status == 'failed',
            SendTask.completed_at >= today_start
        ).count()

        return jsonify({
            "success": True,
            "data": {
                "sender_accounts": sender_stats,
                "monitor_accounts": monitor_stats,
                "whatsapp_accounts": wa_stats,
                "task_queue": {
                    "pending": pending_tasks,
                    "in_progress": in_progress_tasks,
                    "completed_today": completed_today,
                    "failed_today": failed_today,
                }
            }
        })
    finally:
        session.close()


if __name__ == '__main__':
    # 初始化数据库
    init_db()
    logger.info(f"Facebook帖子监控系统启动，端口: {FLASK_PORT}")
    logger.info(f"访问 http://localhost:{FLASK_PORT} 查看控制面板")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True)
