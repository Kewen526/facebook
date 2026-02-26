from datetime import datetime, timezone
from hashlib import sha256
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import DATABASE_URL

Base = declarative_base()


class User(Base):
    """系统用户表 - Admin/经理/员工"""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(128), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False, index=True)  # "admin" / "manager" / "employee"
    parent_id = Column(Integer, ForeignKey('users.id'), nullable=True)  # 上级用户ID（经理→admin, 员工→经理）
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    parent = relationship("User", remote_side=[id], backref="children")

    __table_args__ = (
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def set_password(self, password):
        self.password_hash = sha256(password.encode('utf-8')).hexdigest()

    def check_password(self, password):
        return self.password_hash == sha256(password.encode('utf-8')).hexdigest()

    def get_team_user_ids(self, session):
        """获取该用户所管辖的所有用户ID列表（含自己）"""
        ids = [self.id]
        if self.role == 'admin':
            # admin看所有用户
            all_users = session.query(User).all()
            ids = [u.id for u in all_users]
        elif self.role == 'manager':
            # 经理看自己 + 下属员工
            employees = session.query(User).filter(User.parent_id == self.id).all()
            ids.extend([e.id for e in employees])
        return ids

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "parent_id": self.parent_id,
            "parent_name": self.parent.username if self.parent else None,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WhatsAppAccount(Base):
    """WhatsApp账号表"""
    __tablename__ = 'whatsapp_accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String(32), unique=True, nullable=False)
    enabled = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)  # 被用于私信提示词的次数
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    sender_accounts = relationship("Account", back_populates="whatsapp_account")

    __table_args__ = (
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "phone_number": self.phone_number,
            "enabled": self.enabled,
            "usage_count": self.usage_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Account(Base):
    """账号表 - 统一管理监控和发送账号"""
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)  # 账号名称/邮箱
    account_type = Column(String(32), nullable=False, index=True)  # "monitor" 或 "sender"
    cookie_url = Column(Text)  # Cookie JSON文件URL
    cookie_status = Column(String(32), default='unknown')  # "valid" / "invalid" / "unknown"
    status = Column(String(32), default='active')  # "active" / "banned" / "paused"
    whatsapp_account_id = Column(Integer, ForeignKey('whatsapp_accounts.id'), nullable=True)  # 仅sender使用
    last_task_at = Column(DateTime, nullable=True)  # 上次发送时间(频率限制用)
    rate_limited_until = Column(DateTime, nullable=True)  # 消息限制解除时间(24小时后自动恢复)
    enabled = Column(Boolean, default=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)  # 创建者/所属用户
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    whatsapp_account = relationship("WhatsAppAccount", back_populates="sender_accounts")
    send_tasks = relationship("SendTask", back_populates="account", lazy="dynamic")
    owner = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index('idx_account_type_enabled', 'account_type', 'enabled'),
        Index('idx_account_user', 'user_id'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "account_type": self.account_type,
            "cookie_url": self.cookie_url,
            "cookie_status": self.cookie_status,
            "status": self.status,
            "whatsapp_account_id": self.whatsapp_account_id,
            "whatsapp_phone": self.whatsapp_account.phone_number if self.whatsapp_account else None,
            "last_task_at": self.last_task_at.isoformat() if self.last_task_at else None,
            "rate_limited_until": self.rate_limited_until.isoformat() if self.rate_limited_until else None,
            "enabled": self.enabled,
            "user_id": self.user_id,
            "owner_name": self.owner.username if self.owner else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Post(Base):
    """帖子表 - 存储采集到的Facebook帖子"""
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String(128), unique=True, nullable=False, index=True)  # Facebook帖子ID(去重key)
    post_url = Column(Text)  # 帖子链接
    author_name = Column(String(256))  # 作者显示名
    author_id = Column(String(128), index=True)  # Facebook用户ID
    author_profile_url = Column(Text)  # 作者主页链接
    content = Column(Text)  # 帖子文本内容
    content_zh = Column(Text, nullable=True)  # 中文翻译内容
    image_urls = Column(Text)  # 图片URL列表(JSON格式)
    post_time = Column(String(128))  # 帖子时间(原始显示)
    source_page = Column(String(32), index=True)  # 来源页面: home / groups / search
    ai_result = Column(Text)  # AI分析完整响应
    is_target = Column(Boolean, default=False)  # AI判定结果
    action_interested = Column(Boolean, default=False)  # 是否点了有兴趣
    action_not_interested = Column(Boolean, default=False)  # 是否点了没兴趣
    action_liked = Column(Boolean, default=False)  # 是否点了赞
    discovered_by = Column(String(256), nullable=True, index=True)  # 哪个监控账号发现的
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))  # 采集时间

    # 关联: 一个帖子可以有多个操作记录
    actions = relationship("PostAction", back_populates="post", lazy="dynamic")
    send_tasks = relationship("SendTask", back_populates="post", lazy="dynamic")
    feedbacks = relationship("PostFeedback", back_populates="post", lazy="dynamic")

    __table_args__ = (
        Index('idx_source_created', 'source_page', 'created_at'),
        Index('idx_is_target', 'is_target'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "post_id": self.post_id,
            "post_url": self.post_url,
            "author_name": self.author_name,
            "author_id": self.author_id,
            "author_profile_url": self.author_profile_url,
            "content": self.content,
            "content_zh": self.content_zh,
            "image_urls": self.image_urls,
            "post_time": self.post_time,
            "source_page": self.source_page,
            "ai_result": self.ai_result,
            "is_target": self.is_target,
            "action_interested": self.action_interested,
            "action_not_interested": self.action_not_interested,
            "action_liked": self.action_liked,
            "discovered_by": self.discovered_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "actions": [a.to_dict() for a in self.actions] if self.actions else [],
        }


class PostFeedback(Base):
    """帖子反馈表 - 销售人员对帖子的反馈"""
    __tablename__ = 'post_feedbacks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    is_target_manual = Column(Boolean, nullable=True)  # 手动标记是否为目标客户
    is_contacted = Column(Boolean, default=False)  # 是否已联系
    whatsapp_number = Column(String(64), nullable=True)  # 获取到的WhatsApp账号
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    post = relationship("Post", back_populates="feedbacks")
    user = relationship("User")

    __table_args__ = (
        Index('idx_feedback_post_user', 'post_id', 'user_id', unique=True),
        Index('idx_feedback_user', 'user_id'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "post_id": self.post_id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "is_target_manual": self.is_target_manual,
            "is_contacted": self.is_contacted,
            "whatsapp_number": self.whatsapp_number,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PostAction(Base):
    """帖子操作表 - 记录对帖子的操作"""
    __tablename__ = 'post_actions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    account_id = Column(String(256), index=True)  # 发送账号名称
    action_type = Column(String(32))  # 操作类型: message / friend_request / comment
    action_status = Column(String(32), default='pending')  # pending / success / failed
    action_detail = Column(Text)  # 操作详情/错误信息
    send_task_id = Column(Integer, ForeignKey('send_tasks.id'), nullable=True)  # 关联发送任务
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    post = relationship("Post", back_populates="actions")

    __table_args__ = (
        Index('idx_action_account_status', 'account_id', 'action_status'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "post_id": self.post_id,
            "account_id": self.account_id,
            "action_type": self.action_type,
            "action_status": self.action_status,
            "action_detail": self.action_detail,
            "send_task_id": self.send_task_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SendTask(Base):
    """发送任务表 - 管理发送队列"""
    __tablename__ = 'send_tasks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False, index=True)
    task_type = Column(String(32), nullable=False)  # "comment" / "dm" / "add_friend"
    status = Column(String(32), default='pending')  # "pending" / "in_progress" / "completed" / "failed" / "skipped"
    error_message = Column(Text, nullable=True)
    generated_text = Column(Text, nullable=True)  # AI生成的评论/私信内容
    scheduled_at = Column(DateTime, nullable=True)  # 最早执行时间
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    post = relationship("Post", back_populates="send_tasks")
    account = relationship("Account", back_populates="send_tasks")

    __table_args__ = (
        Index('idx_task_status_account', 'status', 'account_id'),
        Index('idx_task_post_account', 'post_id', 'account_id'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "post_id": self.post_id,
            "account_id": self.account_id,
            "account_name": self.account.name if self.account else None,
            "task_type": self.task_type,
            "status": self.status,
            "error_message": self.error_message,
            "generated_text": self.generated_text,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MonitorLog(Base):
    """监控日志表"""
    __tablename__ = 'monitor_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    page_type = Column(String(32))  # home / groups / search
    account_name = Column(String(256), nullable=True)  # 哪个监控账号
    posts_scanned = Column(Integer, default=0)
    posts_new = Column(Integer, default=0)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    __table_args__ = (
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            "id": self.id,
            "page_type": self.page_type,
            "account_name": self.account_name,
            "posts_scanned": self.posts_scanned,
            "posts_new": self.posts_new,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


# ============ 数据库初始化 ============
engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=10, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """创建所有表"""
    Base.metadata.create_all(engine)
    print("数据库表已创建")
    # 创建默认admin账号
    _ensure_admin()


def _ensure_admin():
    """确保存在默认admin账号"""
    session = SessionLocal()
    try:
        admin = session.query(User).filter(User.username == 'admin').first()
        if not admin:
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            session.add(admin)
            session.commit()
            print("已创建默认admin账号 (admin / admin123)")
    except Exception as e:
        session.rollback()
        print(f"创建admin账号失败: {e}")
    finally:
        session.close()


def get_session():
    """获取数据库会话"""
    return SessionLocal()


def is_post_exists(post_id_str):
    """检查帖子是否已存在"""
    session = get_session()
    try:
        exists = session.query(Post).filter(Post.post_id == post_id_str).first() is not None
        return exists
    finally:
        session.close()


def save_post(post_data):
    """保存帖子到数据库"""
    session = get_session()
    try:
        post = Post(**post_data)
        session.add(post)
        session.commit()
        session.refresh(post)
        return post.to_dict()
    except Exception as e:
        session.rollback()
        print(f"保存帖子失败: {e}")
        return None
    finally:
        session.close()


def update_post_action(post_id_str, **kwargs):
    """更新帖子的操作状态"""
    session = get_session()
    try:
        post = session.query(Post).filter(Post.post_id == post_id_str).first()
        if post:
            for key, value in kwargs.items():
                setattr(post, key, value)
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        print(f"更新帖子操作状态失败: {e}")
        return False
    finally:
        session.close()
