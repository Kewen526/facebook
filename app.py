import logging
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

from config import FLASK_PORT
from models import init_db, get_session, Post, PostAction, MonitorLog
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


# ============ 页面路由 ============
@app.route('/')
def index():
    return render_template('index.html')


# ============ API路由 ============
@app.route('/api/status')
def get_status():
    """获取监控状态"""
    return jsonify(monitor_status)


@app.route('/api/start', methods=['POST'])
def start_monitoring():
    """启动监控"""
    if monitor_status["running"]:
        return jsonify({"success": False, "message": "监控已在运行中"})

    success = start_monitor_thread()
    return jsonify({"success": success, "message": "监控已启动" if success else "启动失败"})


@app.route('/api/stop', methods=['POST'])
def stop_monitoring():
    """停止监控"""
    stop_monitor()
    return jsonify({"success": True, "message": "已发送停止信号"})


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
        return jsonify({"success": True, "data": post.to_dict()})
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
        from sqlalchemy import func
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


if __name__ == '__main__':
    # 初始化数据库
    init_db()
    logger.info(f"Facebook帖子监控系统启动，端口: {FLASK_PORT}")
    logger.info(f"访问 http://localhost:{FLASK_PORT} 查看控制面板")
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, threaded=True)
