import logging
import os
import threading
import time
import requests as http_requests

# 禁用系统代理，确保翻译API直连
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template, jsonify, request, session as flask_session
from flask_socketio import SocketIO
from sqlalchemy import func, and_

from config import (
    FLASK_PORT, COOKIES_DIR, ZHIPU_KEY_API, ZHIPU_MODEL
)
from models import (
    init_db, get_session, Post, PostAction, MonitorLog,
    Account, WhatsAppAccount, SendTask, User, PostFeedback
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
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
# 避免Jinja2与Vue.js模板语法冲突
app.jinja_env.variable_start_string = '[['
app.jinja_env.variable_end_string = ']]'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 发送状态（全局）
sending_status = {
    "running": False,
    "accounts": {}
}


# ============ 认证装饰器 ============
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = flask_session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "请先登录"}), 401
        db = get_session()
        try:
            user = db.query(User).filter(User.id == user_id, User.enabled == True).first()
            if not user:
                flask_session.clear()
                return jsonify({"success": False, "message": "用户不存在或已禁用"}), 401
            request.current_user = user
            request.db = db
            result = f(*args, **kwargs)
            return result
        finally:
            db.close()
    return decorated


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if request.current_user.role != 'admin':
            return jsonify({"success": False, "message": "需要管理员权限"}), 403
        return f(*args, **kwargs)
    return decorated


def manager_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if request.current_user.role not in ('admin', 'manager'):
            return jsonify({"success": False, "message": "需要经理及以上权限"}), 403
        return f(*args, **kwargs)
    return decorated


def _get_team_user_ids(user, db):
    """获取用户有权查看的所有用户ID"""
    return user.get_team_user_ids(db)


def _get_team_account_names(user, db):
    """获取用户有权查看的所有Facebook监控账号名"""
    user_ids = _get_team_user_ids(user, db)
    accounts = db.query(Account.name).filter(Account.user_id.in_(user_ids)).all()
    return [a[0] for a in accounts]


# ============ 页面路由 ============
@app.route('/')
def index():
    return render_template('index.html')


# ============ 认证 API ============
@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"success": False, "message": "请输入用户名和密码"}), 400

    db = get_session()
    try:
        user = db.query(User).filter(User.username == data['username']).first()
        if not user or not user.check_password(data['password']):
            return jsonify({"success": False, "message": "用户名或密码错误"}), 401
        if not user.enabled:
            return jsonify({"success": False, "message": "账号已被禁用"}), 403

        flask_session.permanent = True
        flask_session['user_id'] = user.id
        flask_session['username'] = user.username
        flask_session['role'] = user.role

        return jsonify({
            "success": True,
            "message": "登录成功",
            "data": user.to_dict()
        })
    finally:
        db.close()


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    flask_session.clear()
    return jsonify({"success": True, "message": "已退出登录"})


@app.route('/api/auth/me')
@login_required
def auth_me():
    return jsonify({"success": True, "data": request.current_user.to_dict()})


@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def auth_change_password():
    data = request.get_json()
    if not data or not data.get('old_password') or not data.get('new_password'):
        return jsonify({"success": False, "message": "请输入旧密码和新密码"}), 400

    user = request.current_user
    db = request.db
    if not user.check_password(data['old_password']):
        return jsonify({"success": False, "message": "旧密码错误"}), 400

    user.set_password(data['new_password'])
    db.commit()
    return jsonify({"success": True, "message": "密码修改成功"})


# ============ 用户管理 API ============
@app.route('/api/users', methods=['GET'])
@login_required
def list_users():
    user = request.current_user
    db = request.db

    if user.role == 'admin':
        users = db.query(User).order_by(User.created_at.desc()).all()
    elif user.role == 'manager':
        users = db.query(User).filter(
            (User.id == user.id) | (User.parent_id == user.id)
        ).order_by(User.created_at.desc()).all()
    else:
        users = [user]

    return jsonify({"success": True, "data": [u.to_dict() for u in users]})


@app.route('/api/users', methods=['POST'])
@login_required
def create_user():
    user = request.current_user
    db = request.db
    data = request.get_json()

    if not data or not data.get('username') or not data.get('password') or not data.get('role'):
        return jsonify({"success": False, "message": "缺少必要参数"}), 400

    new_role = data['role']
    # 权限检查
    if user.role == 'admin' and new_role not in ('manager', 'employee'):
        return jsonify({"success": False, "message": "Admin只能创建经理或员工"}), 400
    if user.role == 'manager' and new_role != 'employee':
        return jsonify({"success": False, "message": "经理只能创建员工"}), 400
    if user.role == 'employee':
        return jsonify({"success": False, "message": "员工无权创建用户"}), 403

    # 检查用户名唯一
    existing = db.query(User).filter(User.username == data['username']).first()
    if existing:
        return jsonify({"success": False, "message": "用户名已存在"}), 400

    # 确定parent_id
    if user.role == 'admin' and new_role == 'manager':
        parent_id = user.id
    elif user.role == 'admin' and new_role == 'employee':
        # admin创建员工时需要指定经理
        parent_id = data.get('parent_id')
        if not parent_id:
            return jsonify({"success": False, "message": "创建员工时需要指定所属经理"}), 400
        manager = db.query(User).filter(User.id == parent_id, User.role == 'manager').first()
        if not manager:
            return jsonify({"success": False, "message": "指定的经理不存在"}), 400
    elif user.role == 'manager':
        parent_id = user.id
    else:
        parent_id = user.id

    new_user = User(
        username=data['username'],
        role=new_role,
        parent_id=parent_id,
        enabled=data.get('enabled', True),
    )
    new_user.set_password(data['password'])

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return jsonify({"success": True, "data": new_user.to_dict(), "message": "用户创建成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"创建失败: {e}"}), 500


@app.route('/api/users/<int:uid>', methods=['PUT'])
@login_required
def update_user(uid):
    user = request.current_user
    db = request.db
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "缺少更新数据"}), 400

    target = db.query(User).filter(User.id == uid).first()
    if not target:
        return jsonify({"success": False, "message": "用户不存在"}), 404

    # 权限检查
    if user.role == 'manager' and target.parent_id != user.id and target.id != user.id:
        return jsonify({"success": False, "message": "无权修改此用户"}), 403
    if user.role == 'employee' and target.id != user.id:
        return jsonify({"success": False, "message": "无权修改此用户"}), 403

    if 'enabled' in data:
        target.enabled = data['enabled']
    if 'password' in data and data['password']:
        target.set_password(data['password'])

    try:
        db.commit()
        db.refresh(target)
        return jsonify({"success": True, "data": target.to_dict(), "message": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"更新失败: {e}"}), 500


@app.route('/api/users/<int:uid>', methods=['DELETE'])
@admin_required
def delete_user(uid):
    db = request.db
    target = db.query(User).filter(User.id == uid).first()
    if not target:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    if target.role == 'admin':
        return jsonify({"success": False, "message": "不能删除admin账号"}), 400

    # 检查是否有下属
    children = db.query(User).filter(User.parent_id == uid).count()
    if children > 0:
        return jsonify({"success": False, "message": f"该用户有 {children} 个下属，请先处理下属账号"}), 400

    try:
        db.delete(target)
        db.commit()
        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"删除失败: {e}"}), 500


# ============ 监控 API ============
@app.route('/api/status')
@login_required
def get_status():
    return jsonify(monitor_status)


@app.route('/api/start', methods=['POST'])
@login_required
def start_monitoring():
    if monitor_status.get("running"):
        return jsonify({"success": False, "message": "监控已在运行中"})
    success = start_monitor_thread()
    return jsonify({"success": success, "message": "监控已启动" if success else "启动失败"})


@app.route('/api/stop', methods=['POST'])
@login_required
def stop_monitoring():
    stop_monitor()
    return jsonify({"success": True, "message": "已发送停止信号"})


# ============ 帖子 API (带数据隔离) ============
@app.route('/api/posts')
@login_required
def get_posts():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    source = request.args.get('source', '')
    is_target = request.args.get('is_target', '')
    search = request.args.get('search', '')
    feedback_filter = request.args.get('feedback_filter', '')

    user = request.current_user
    db = request.db

    query = db.query(Post).order_by(Post.created_at.desc())

    # 数据隔离：非admin只能看自己团队的帖子
    if user.role != 'admin':
        account_names = _get_team_account_names(user, db)
        if account_names:
            query = query.filter(Post.discovered_by.in_(account_names))
        else:
            # 没有关联账号则看不到帖子
            return jsonify({"success": True, "data": [], "total": 0, "page": page, "per_page": per_page, "total_pages": 0})

    if source:
        query = query.filter(Post.source_page == source)
    if is_target == 'true':
        query = query.filter(Post.is_target == True)
    elif is_target == 'false':
        query = query.filter(Post.is_target == False)
    if search:
        query = query.filter(Post.content.ilike(f'%{search}%'))

    # 反馈筛选：通过join PostFeedback过滤
    if feedback_filter == 'manual_target':
        query = query.join(PostFeedback, Post.id == PostFeedback.post_id).filter(PostFeedback.is_target_manual == True)
    elif feedback_filter == 'contacted':
        query = query.join(PostFeedback, Post.id == PostFeedback.post_id).filter(PostFeedback.is_contacted == True)
    elif feedback_filter == 'has_whatsapp':
        query = query.join(PostFeedback, Post.id == PostFeedback.post_id).filter(
            PostFeedback.whatsapp_number != None,
            PostFeedback.whatsapp_number != ''
        )

    total = query.count()
    posts = query.offset((page - 1) * per_page).limit(per_page).all()

    # 附加当前用户的反馈信息
    post_ids = [p.id for p in posts]
    feedbacks = {}
    if post_ids:
        fb_list = db.query(PostFeedback).filter(
            PostFeedback.post_id.in_(post_ids),
            PostFeedback.user_id == user.id
        ).all()
        for fb in fb_list:
            feedbacks[fb.post_id] = fb.to_dict()

    result = []
    for p in posts:
        d = p.to_dict()
        d['my_feedback'] = feedbacks.get(p.id)
        result.append(d)

    return jsonify({
        "success": True,
        "data": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    })


@app.route('/api/posts/<int:post_db_id>')
@login_required
def get_post_detail(post_db_id):
    db = request.db
    user = request.current_user
    post = db.query(Post).filter(Post.id == post_db_id).first()
    if not post:
        return jsonify({"success": False, "message": "帖子不存在"}), 404

    data = post.to_dict()
    tasks = db.query(SendTask).filter(SendTask.post_id == post_db_id).all()
    data["send_tasks"] = [t.to_dict() for t in tasks]

    # 附加当前用户反馈
    fb = db.query(PostFeedback).filter(
        PostFeedback.post_id == post_db_id,
        PostFeedback.user_id == user.id
    ).first()
    data['my_feedback'] = fb.to_dict() if fb else None

    # 附加所有反馈（经理/admin可看）
    if user.role in ('admin', 'manager'):
        all_fbs = db.query(PostFeedback).filter(PostFeedback.post_id == post_db_id).all()
        data['all_feedbacks'] = [f.to_dict() for f in all_fbs]

    return jsonify({"success": True, "data": data})


@app.route('/api/posts/<int:post_db_id>/actions')
@login_required
def get_post_actions(post_db_id):
    db = request.db
    actions = db.query(PostAction).filter(
        PostAction.post_id == post_db_id
    ).order_by(PostAction.created_at.desc()).all()
    return jsonify({"success": True, "data": [a.to_dict() for a in actions]})


# ============ 帖子反馈 API ============
@app.route('/api/posts/<int:post_db_id>/feedback', methods=['POST'])
@login_required
def submit_feedback(post_db_id):
    user = request.current_user
    db = request.db
    data = request.get_json()

    post = db.query(Post).filter(Post.id == post_db_id).first()
    if not post:
        return jsonify({"success": False, "message": "帖子不存在"}), 404

    # 查找或创建反馈
    fb = db.query(PostFeedback).filter(
        PostFeedback.post_id == post_db_id,
        PostFeedback.user_id == user.id
    ).first()

    if not fb:
        fb = PostFeedback(post_id=post_db_id, user_id=user.id)
        db.add(fb)

    if 'is_target_manual' in data:
        fb.is_target_manual = data['is_target_manual']
    if 'is_contacted' in data:
        fb.is_contacted = data['is_contacted']
    if 'whatsapp_number' in data:
        fb.whatsapp_number = data['whatsapp_number'] if data['whatsapp_number'] else None

    try:
        db.commit()
        db.refresh(fb)
        return jsonify({"success": True, "data": fb.to_dict(), "message": "反馈已保存"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"保存失败: {e}"}), 500


@app.route('/api/posts/<int:post_db_id>/feedback', methods=['GET'])
@login_required
def get_feedback(post_db_id):
    user = request.current_user
    db = request.db

    if user.role == 'admin':
        fbs = db.query(PostFeedback).filter(PostFeedback.post_id == post_db_id).all()
    elif user.role == 'manager':
        team_ids = _get_team_user_ids(user, db)
        fbs = db.query(PostFeedback).filter(
            PostFeedback.post_id == post_db_id,
            PostFeedback.user_id.in_(team_ids)
        ).all()
    else:
        fbs = db.query(PostFeedback).filter(
            PostFeedback.post_id == post_db_id,
            PostFeedback.user_id == user.id
        ).all()

    return jsonify({"success": True, "data": [f.to_dict() for f in fbs]})


# ============ 帖子翻译 API ============
@app.route('/api/posts/<int:post_db_id>/translate', methods=['POST'])
@login_required
def translate_post(post_db_id):
    db = request.db
    post = db.query(Post).filter(Post.id == post_db_id).first()
    if not post:
        return jsonify({"success": False, "message": "帖子不存在"}), 404

    if post.content_zh and post.content_zh != '[翻译失败]':
        return jsonify({"success": True, "data": {"content_zh": post.content_zh}, "message": "已有翻译"})

    if not post.content or not post.content.strip():
        return jsonify({"success": False, "message": "帖子内容为空"}), 400

    # 调用AI翻译
    try:
        content_zh = _translate_to_chinese(post.content)
        if content_zh:
            post.content_zh = content_zh
            db.commit()
            return jsonify({"success": True, "data": {"content_zh": content_zh}, "message": "翻译成功"})
        else:
            return jsonify({"success": False, "message": "翻译失败"}), 500
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"翻译失败: {e}"}), 500


def _translate_to_chinese(text):
    """使用QQ翻译API将文本翻译为中文（自动检测语言）"""
    try:
        if not text or not text.strip():
            return None
        # 截取前2000字符
        text = text[:2000]

        resp = http_requests.get(
            'https://api.lolimi.cn/API/qqfy/api',
            params={'msg': text, 'type': 'json'},
            proxies={'http': None, 'https': None},
            timeout=30
        )
        result = resp.json()

        if result.get('code') == 1 and result.get('text'):
            return result['text']
        else:
            logger.error(f"QQ翻译API错误: {result}")
            return None
    except Exception as e:
        logger.error(f"QQ翻译失败: {e}")
        return None


# ============ 后台自动翻译线程 ============
_translate_thread = None
_translate_stop = threading.Event()


def _auto_translate_worker():
    """后台线程：自动翻译所有未翻译的帖子，优先目标客户+最新日期"""
    logger.info("自动翻译线程已启动")
    _retry_counter = 0
    while not _translate_stop.is_set():
        try:
            db = get_session()
            try:
                # 优先翻译目标客户的未翻译帖子，再翻译非目标客户的
                # 分两次查询避免 ORDER BY 多列导致 sort_buffer 溢出
                untranslated = db.query(Post).filter(
                    Post.content != None,
                    Post.content != '',
                    Post.content_zh == None,
                    Post.is_target == True
                ).order_by(Post.id.desc()).limit(10).all()

                if not untranslated:
                    untranslated = db.query(Post).filter(
                        Post.content != None,
                        Post.content != '',
                        Post.content_zh == None
                    ).order_by(Post.id.desc()).limit(10).all()

                # 每10轮（约5分钟）也重试翻译失败的帖子
                if not untranslated:
                    _retry_counter += 1
                    if _retry_counter >= 10:
                        _retry_counter = 0
                        failed = db.query(Post).filter(
                            Post.content_zh == '[翻译失败]'
                        ).order_by(Post.id.desc()).limit(5).all()
                        if failed:
                            logger.info(f"重试 {len(failed)} 条翻译失败的帖子")
                            untranslated = failed
                        else:
                            _translate_stop.wait(30)
                            continue
                    else:
                        _translate_stop.wait(30)
                        continue

                for post in untranslated:
                    if _translate_stop.is_set():
                        break
                    try:
                        content_zh = _translate_to_chinese(post.content)
                        if content_zh:
                            post.content_zh = content_zh
                            db.commit()
                            tag = "目标客户" if post.is_target else "非目标"
                            logger.info(f"自动翻译成功: 帖子ID={post.id} [{tag}]")
                        else:
                            post.content_zh = '[翻译失败]'
                            db.commit()
                            logger.warning(f"自动翻译失败: 帖子ID={post.id}")
                    except Exception as e:
                        db.rollback()
                        logger.error(f"翻译帖子 {post.id} 出错: {e}")
                    # 翻译间隔，避免请求过快
                    _translate_stop.wait(0.5)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"自动翻译线程异常: {e}")
            _translate_stop.wait(10)

    logger.info("自动翻译线程已停止")


def start_auto_translate():
    """启动自动翻译后台线程"""
    global _translate_thread
    if _translate_thread and _translate_thread.is_alive():
        return
    _translate_stop.clear()
    _translate_thread = threading.Thread(target=_auto_translate_worker, daemon=True, name="auto-translate")
    _translate_thread.start()


@app.route('/api/translate/retry', methods=['POST'])
@login_required
def retry_failed_translations():
    """重置所有翻译失败的帖子，让后台线程重新翻译"""
    user = request.current_user
    if user.role != 'admin':
        return jsonify({"success": False, "message": "仅管理员可操作"}), 403
    db = request.db
    count = db.query(Post).filter(Post.content_zh == '[翻译失败]').update({Post.content_zh: None})
    db.commit()
    return jsonify({"success": True, "message": f"已重置 {count} 条翻译失败记录，后台线程将自动重新翻译"})


@app.route('/api/translate/test')
@login_required
def test_translation():
    """测试QQ翻译API是否可用"""
    user = request.current_user
    if user.role != 'admin':
        return jsonify({"success": False, "message": "仅管理员可操作"}), 403
    try:
        resp = http_requests.get(
            'https://api.lolimi.cn/API/qqfy/api',
            params={'msg': 'Hello, this is a test.', 'type': 'json'},
            proxies={'http': None, 'https': None},
            timeout=30
        )
        result = resp.json()
        if result.get('code') == 1 and result.get('text'):
            return jsonify({"success": True, "message": f"QQ翻译API正常，测试结果: {result['text']}", "result": result})
        else:
            return jsonify({"success": False, "message": f"API错误: {result}", "result": result})
    except Exception as e:
        return jsonify({"success": False, "message": f"请求失败: {e}"})


@app.route('/api/stats')
@login_required
def get_stats():
    user = request.current_user
    db = request.db

    if user.role == 'admin':
        base_query = db.query(Post)
    else:
        account_names = _get_team_account_names(user, db)
        if account_names:
            base_query = db.query(Post).filter(Post.discovered_by.in_(account_names))
        else:
            return jsonify({"success": True, "data": {"total_posts": 0, "target_posts": 0, "non_target_posts": 0, "liked_posts": 0, "interested_posts": 0, "source_stats": {}, "recent_logs": []}})

    total_posts = base_query.count()
    target_posts = base_query.filter(Post.is_target == True).count()
    liked_posts = base_query.filter(Post.action_liked == True).count()
    interested_posts = base_query.filter(Post.action_interested == True).count()

    source_stats = db.query(
        Post.source_page, func.count(Post.id)
    ).group_by(Post.source_page).all()

    recent_logs = db.query(MonitorLog).order_by(MonitorLog.id.desc()).limit(10).all()

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


@app.route('/api/logs')
@login_required
def get_logs():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    db = request.db

    query = db.query(MonitorLog).order_by(MonitorLog.id.desc())
    total = query.count()
    logs = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        "success": True,
        "data": [l.to_dict() for l in logs],
        "total": total,
    })


# ============ 账号管理 API (带数据隔离) ============
@app.route('/api/accounts', methods=['GET'])
@login_required
def list_accounts():
    account_type = request.args.get('type', '')
    user = request.current_user
    db = request.db

    if user.role == 'admin':
        query = db.query(Account)
    else:
        user_ids = _get_team_user_ids(user, db)
        query = db.query(Account).filter(Account.user_id.in_(user_ids))

    if account_type:
        query = query.filter(Account.account_type == account_type)
    accounts = query.order_by(Account.created_at.desc()).all()
    return jsonify({"success": True, "data": [a.to_dict() for a in accounts]})


@app.route('/api/accounts', methods=['POST'])
@login_required
def create_account():
    data = request.get_json()
    user = request.current_user
    db = request.db

    if not data or not data.get('name') or not data.get('account_type'):
        return jsonify({"success": False, "message": "缺少必要参数 name 和 account_type"}), 400
    if data['account_type'] not in ('monitor', 'sender'):
        return jsonify({"success": False, "message": "account_type 必须是 monitor 或 sender"}), 400

    existing = db.query(Account).filter(Account.name == data['name']).first()
    if existing:
        return jsonify({"success": False, "message": "账号名称已存在"}), 400

    try:
        account = Account(
            name=data['name'],
            account_type=data['account_type'],
            cookie_url=data.get('cookie_url', ''),
            enabled=data.get('enabled', True),
            whatsapp_account_id=data.get('whatsapp_account_id'),
            user_id=user.id,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return jsonify({"success": True, "data": account.to_dict(), "message": "账号创建成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"创建失败: {e}"}), 500


@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
@login_required
def update_account(account_id):
    data = request.get_json()
    db = request.db
    if not data:
        return jsonify({"success": False, "message": "缺少更新数据"}), 400

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return jsonify({"success": False, "message": "账号不存在"}), 404

    try:
        for field in ['name', 'cookie_url', 'cookie_status', 'status', 'enabled', 'whatsapp_account_id']:
            if field in data:
                setattr(account, field, data[field])
        db.commit()
        db.refresh(account)
        return jsonify({"success": True, "data": account.to_dict(), "message": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"更新失败: {e}"}), 500


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    db = request.db
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return jsonify({"success": False, "message": "账号不存在"}), 404
    try:
        db.delete(account)
        db.commit()
        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"删除失败: {e}"}), 500


@app.route('/api/accounts/<int:account_id>/refresh-cookie', methods=['POST'])
@login_required
def refresh_account_cookie(account_id):
    db = request.db
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return jsonify({"success": False, "message": "账号不存在"}), 404
    if not account.cookie_url:
        return jsonify({"success": False, "message": "该账号没有设置Cookie URL"}), 400

    success, msg = _download_cookie_from_url(account.cookie_url, account.name)
    try:
        if success:
            account.cookie_status = 'valid'
            db.commit()
            return jsonify({"success": True, "message": "Cookie已刷新"})
        else:
            account.cookie_status = 'invalid'
            db.commit()
            return jsonify({"success": False, "message": f"Cookie下载失败: {msg}"}), 500
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"刷新失败: {e}"}), 500


# ============ Cookie上传 API ============
@app.route('/api/cookies/upload', methods=['POST'])
@login_required
def upload_cookie():
    data = request.get_json()
    user = request.current_user
    db = request.db

    if not data or not data.get('cookie_url'):
        return jsonify({"success": False, "message": "缺少 cookie_url 参数"}), 400
    if not data.get('account_type') or data['account_type'] not in ('monitor', 'sender'):
        return jsonify({"success": False, "message": "account_type 必须是 monitor 或 sender"}), 400

    cookie_url = data['cookie_url']
    account_name = data.get('account_name', f"{data['account_type']}_{datetime.now().strftime('%Y%m%d%H%M%S')}")

    success, msg = _download_cookie_from_url(cookie_url, account_name)
    if not success:
        return jsonify({"success": False, "message": f"Cookie下载失败: {msg}"}), 400

    try:
        account = db.query(Account).filter(Account.name == account_name).first()
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
                user_id=user.id,
            )
            db.add(account)
        db.commit()
        db.refresh(account)
        return jsonify({"success": True, "data": account.to_dict(), "message": "Cookie上传成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"保存失败: {e}"}), 500


def _download_cookie_from_url(cookie_url, account_name):
    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)
        resp = http_requests.get(cookie_url, timeout=30, proxies={'http': None, 'https': None})
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
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
@login_required
def list_whatsapp_accounts():
    db = request.db
    accounts = db.query(WhatsAppAccount).order_by(WhatsAppAccount.created_at.desc()).all()
    return jsonify({"success": True, "data": [a.to_dict() for a in accounts]})


@app.route('/api/whatsapp-accounts', methods=['POST'])
@login_required
def create_whatsapp_account():
    data = request.get_json()
    db = request.db
    if not data or not data.get('phone_number'):
        return jsonify({"success": False, "message": "缺少 phone_number 参数"}), 400

    try:
        existing = db.query(WhatsAppAccount).filter(
            WhatsAppAccount.phone_number == data['phone_number']
        ).first()
        if existing:
            return jsonify({"success": False, "message": "该WhatsApp号码已存在"}), 400

        wa = WhatsAppAccount(phone_number=data['phone_number'], enabled=data.get('enabled', True))
        db.add(wa)
        db.commit()
        db.refresh(wa)
        return jsonify({"success": True, "data": wa.to_dict(), "message": "WhatsApp账号创建成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"创建失败: {e}"}), 500


@app.route('/api/whatsapp-accounts/<int:wa_id>', methods=['PUT'])
@login_required
def update_whatsapp_account(wa_id):
    data = request.get_json()
    db = request.db
    if not data:
        return jsonify({"success": False, "message": "缺少更新数据"}), 400

    try:
        wa = db.query(WhatsAppAccount).filter(WhatsAppAccount.id == wa_id).first()
        if not wa:
            return jsonify({"success": False, "message": "WhatsApp账号不存在"}), 404
        for field in ['phone_number', 'enabled']:
            if field in data:
                setattr(wa, field, data[field])
        db.commit()
        db.refresh(wa)
        return jsonify({"success": True, "data": wa.to_dict(), "message": "更新成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"更新失败: {e}"}), 500


@app.route('/api/whatsapp-accounts/<int:wa_id>', methods=['DELETE'])
@login_required
def delete_whatsapp_account(wa_id):
    db = request.db
    try:
        wa = db.query(WhatsAppAccount).filter(WhatsAppAccount.id == wa_id).first()
        if not wa:
            return jsonify({"success": False, "message": "WhatsApp账号不存在"}), 404
        linked = db.query(Account).filter(Account.whatsapp_account_id == wa_id).count()
        if linked > 0:
            return jsonify({"success": False, "message": f"该WhatsApp号码被 {linked} 个发送账号关联，请先解除关联"}), 400
        db.delete(wa)
        db.commit()
        return jsonify({"success": True, "message": "删除成功"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": f"删除失败: {e}"}), 500


# ============ 发送控制 API ============
@app.route('/api/sending/start', methods=['POST'])
@login_required
def start_sending():
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
@login_required
def stop_sending():
    try:
        from task_queue import stop_task_processor
        stop_task_processor()
        sending_status["running"] = False
        return jsonify({"success": True, "message": "已发送停止信号"})
    except Exception as e:
        return jsonify({"success": False, "message": f"停止失败: {e}"}), 500


@app.route('/api/sending/status')
@login_required
def get_sending_status():
    try:
        from task_queue import get_all_sender_status
        status = get_all_sender_status()
        return jsonify({"success": True, "data": status})
    except Exception:
        return jsonify({"success": True, "data": {"running": False, "accounts": {}}})


@app.route('/api/sending/tasks')
@login_required
def get_sending_tasks():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    status_filter = request.args.get('status', '')
    task_type = request.args.get('task_type', '')
    account_id = request.args.get('account_id', '', type=str)

    db = request.db
    query = db.query(SendTask).order_by(SendTask.created_at.desc())
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


# ============ 统计面板 API (带数据隔离和日期过滤) ============
@app.route('/api/stats/dashboard')
@login_required
def get_dashboard_stats():
    db = request.db
    user = request.current_user
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 发送账号统计
    if user.role == 'admin':
        sender_accounts = db.query(Account).filter(Account.account_type == 'sender').all()
    else:
        user_ids = _get_team_user_ids(user, db)
        sender_accounts = db.query(Account).filter(
            Account.account_type == 'sender',
            Account.user_id.in_(user_ids)
        ).all()

    sender_stats = []
    for sa in sender_accounts:
        total_comments = db.query(SendTask).filter(SendTask.account_id == sa.id, SendTask.task_type == 'comment', SendTask.status == 'completed').count()
        total_dms = db.query(SendTask).filter(SendTask.account_id == sa.id, SendTask.task_type == 'dm', SendTask.status == 'completed').count()
        daily_dms = db.query(SendTask).filter(SendTask.account_id == sa.id, SendTask.task_type == 'dm', SendTask.status == 'completed', SendTask.completed_at >= today_start).count()
        daily_friends = db.query(SendTask).filter(SendTask.account_id == sa.id, SendTask.task_type == 'add_friend', SendTask.status == 'completed', SendTask.completed_at >= today_start).count()

        sender_stats.append({
            "id": sa.id, "name": sa.name, "status": sa.status, "enabled": sa.enabled,
            "whatsapp_phone": sa.whatsapp_account.phone_number if sa.whatsapp_account else None,
            "total_comments": total_comments, "total_dms": total_dms,
            "daily_dms": daily_dms, "daily_friend_requests": daily_friends,
        })

    # 监控账号统计
    if user.role == 'admin':
        monitor_accounts = db.query(Account).filter(Account.account_type == 'monitor').all()
    else:
        user_ids = _get_team_user_ids(user, db)
        monitor_accounts = db.query(Account).filter(
            Account.account_type == 'monitor',
            Account.user_id.in_(user_ids)
        ).all()

    monitor_stats = []
    for ma in monitor_accounts:
        target_count = db.query(Post).filter(Post.discovered_by == ma.name, Post.is_target == True).count()
        total_count = db.query(Post).filter(Post.discovered_by == ma.name).count()
        monitor_stats.append({
            "id": ma.id, "name": ma.name, "enabled": ma.enabled,
            "target_posts_found": target_count, "total_posts_found": total_count,
        })

    wa_accounts = db.query(WhatsAppAccount).all()
    wa_stats = [a.to_dict() for a in wa_accounts]

    pending_tasks = db.query(SendTask).filter(SendTask.status == 'pending').count()
    in_progress_tasks = db.query(SendTask).filter(SendTask.status == 'in_progress').count()
    completed_today = db.query(SendTask).filter(SendTask.status == 'completed', SendTask.completed_at >= today_start).count()
    failed_today = db.query(SendTask).filter(SendTask.status == 'failed', SendTask.completed_at >= today_start).count()

    return jsonify({
        "success": True,
        "data": {
            "sender_accounts": sender_stats,
            "monitor_accounts": monitor_stats,
            "whatsapp_accounts": wa_stats,
            "task_queue": {
                "pending": pending_tasks, "in_progress": in_progress_tasks,
                "completed_today": completed_today, "failed_today": failed_today,
            }
        }
    })


# ============ 新增：按日期统计面板 API ============
@app.route('/api/stats/report')
@login_required
def get_stats_report():
    """按日期范围统计 - 包含反馈数据"""
    user = request.current_user
    db = request.db
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    # 解析日期
    try:
        if start_date:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        else:
            start_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
        if end_date:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        else:
            end_dt = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
    except ValueError:
        return jsonify({"success": False, "message": "日期格式错误，请使用 YYYY-MM-DD"}), 400

    # 按角色获取可查看的用户ID
    if user.role == 'admin':
        team_user_ids = None  # 不过滤
    else:
        team_user_ids = _get_team_user_ids(user, db)

    # 帖子统计（按团队账号过滤）
    post_query = db.query(Post).filter(Post.created_at >= start_dt, Post.created_at <= end_dt)
    if user.role != 'admin':
        account_names = _get_team_account_names(user, db)
        if account_names:
            post_query = post_query.filter(Post.discovered_by.in_(account_names))
        else:
            post_query = post_query.filter(Post.id == -1)  # 无数据

    total_posts = post_query.count()
    target_posts = post_query.filter(Post.is_target == True).count()

    # 反馈统计
    fb_query = db.query(PostFeedback).filter(
        PostFeedback.created_at >= start_dt,
        PostFeedback.created_at <= end_dt
    )
    if team_user_ids is not None:
        fb_query = fb_query.filter(PostFeedback.user_id.in_(team_user_ids))

    marked_target = fb_query.filter(PostFeedback.is_target_manual == True).count()
    contacted = fb_query.filter(PostFeedback.is_contacted == True).count()
    got_whatsapp = fb_query.filter(PostFeedback.whatsapp_number != None, PostFeedback.whatsapp_number != '').count()

    # WhatsApp详情
    whatsapp_details = fb_query.filter(
        PostFeedback.whatsapp_number != None,
        PostFeedback.whatsapp_number != ''
    ).all()

    whatsapp_list = []
    for fb in whatsapp_details:
        post = db.query(Post).filter(Post.id == fb.post_id).first()
        whatsapp_list.append({
            "feedback_id": fb.id,
            "post_id": fb.post_id,
            "author_name": post.author_name if post else None,
            "post_url": post.post_url if post else None,
            "whatsapp_number": fb.whatsapp_number,
            "username": fb.user.username if fb.user else None,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        })

    return jsonify({
        "success": True,
        "data": {
            "start_date": start_dt.strftime('%Y-%m-%d'),
            "end_date": end_dt.strftime('%Y-%m-%d'),
            "total_posts": total_posts,
            "target_posts": target_posts,
            "marked_target": marked_target,
            "contacted": contacted,
            "got_whatsapp": got_whatsapp,
            "whatsapp_details": whatsapp_list,
        }
    })


if __name__ == '__main__':
    init_db()
    start_auto_translate()
    logger.info(f"Facebook帖子监控系统启动，端口: {FLASK_PORT}")
    logger.info(f"访问 http://localhost:{FLASK_PORT} 查看控制面板")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True)
