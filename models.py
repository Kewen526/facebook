from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import DATABASE_URL

Base = declarative_base()


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
    image_urls = Column(Text)  # 图片URL列表(JSON格式)
    post_time = Column(String(128))  # 帖子时间(原始显示)
    source_page = Column(String(32), index=True)  # 来源页面: home / groups / search
    ai_result = Column(Text)  # AI分析完整响应
    is_target = Column(Boolean, default=False)  # AI判定结果
    action_interested = Column(Boolean, default=False)  # 是否点了有兴趣
    action_not_interested = Column(Boolean, default=False)  # 是否点了没兴趣
    action_liked = Column(Boolean, default=False)  # 是否点了赞
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))  # 采集时间

    # 关联: 一个帖子可以有多个操作记录
    actions = relationship("PostAction", back_populates="post", lazy="dynamic")

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
            "image_urls": self.image_urls,
            "post_time": self.post_time,
            "source_page": self.source_page,
            "ai_result": self.ai_result,
            "is_target": self.is_target,
            "action_interested": self.action_interested,
            "action_not_interested": self.action_not_interested,
            "action_liked": self.action_liked,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "actions": [a.to_dict() for a in self.actions] if self.actions else [],
        }


class PostAction(Base):
    """帖子操作表 - 为未来多账号发送设计"""
    __tablename__ = 'post_actions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    account_id = Column(String(256), index=True)  # 发送账号
    action_type = Column(String(32))  # 操作类型: message / friend_request / comment
    action_status = Column(String(32), default='pending')  # pending / success / failed
    action_detail = Column(Text)  # 操作详情/错误信息
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MonitorLog(Base):
    """监控日志表"""
    __tablename__ = 'monitor_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    page_type = Column(String(32))  # home / groups / search
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
