#!/usr/bin/env python3
"""
数据库迁移脚本 - 使用pymysql执行（无需安装mysql客户端）
用法: python3 migrate_db.py
"""
import pymysql
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

def run_migration():
    print(f"连接数据库 {DB_HOST}:{DB_PORT}/{DB_NAME} ...")
    conn = pymysql.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset='utf8mb4',
    )
    cursor = conn.cursor()

    migrations = [
        # 1. 创建 users 表
        (
            "users",
            """CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(128) NOT NULL UNIQUE,
                password_hash VARCHAR(128) NOT NULL,
                role VARCHAR(32) NOT NULL,
                parent_id INT DEFAULT NULL,
                enabled TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_role (role),
                FOREIGN KEY (parent_id) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
        ),
        # 2. 创建 post_feedbacks 表
        (
            "post_feedbacks",
            """CREATE TABLE IF NOT EXISTS post_feedbacks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                post_id INT NOT NULL,
                user_id INT NOT NULL,
                is_target_manual TINYINT(1) DEFAULT NULL,
                is_contacted TINYINT(1) DEFAULT 0,
                whatsapp_number VARCHAR(64) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE INDEX idx_feedback_post_user (post_id, user_id),
                INDEX idx_feedback_user (user_id),
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
        ),
    ]

    # 需要添加的列（ALTER TABLE）
    alter_columns = [
        # (表名, 列名, 列定义)
        ("posts", "content_zh", "TEXT DEFAULT NULL COMMENT '中文翻译'"),
        ("accounts", "user_id", "INT DEFAULT NULL COMMENT '所属用户ID'"),
    ]

    # 执行建表
    for name, sql in migrations:
        try:
            cursor.execute(sql)
            conn.commit()
            print(f"  [OK] 表 {name} 已创建/已存在")
        except Exception as e:
            print(f"  [WARN] 表 {name}: {e}")
            conn.rollback()

    # 执行ALTER TABLE添加列
    for table, col, col_def in alter_columns:
        try:
            # 先检查列是否已存在
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                (DB_NAME, table, col)
            )
            exists = cursor.fetchone()[0]
            if exists:
                print(f"  [SKIP] {table}.{col} 已存在")
            else:
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {col_def}")
                conn.commit()
                print(f"  [OK] 已添加 {table}.{col}")
        except Exception as e:
            print(f"  [WARN] {table}.{col}: {e}")
            conn.rollback()

    # 添加索引优化翻译查询
    indexes = [
        ("posts", "idx_posts_content_zh", "content_zh"),
        ("posts", "idx_posts_is_target", "is_target"),
    ]
    for table, idx_name, col in indexes:
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s",
                (DB_NAME, table, idx_name)
            )
            exists = cursor.fetchone()[0]
            if exists:
                print(f"  [SKIP] 索引 {idx_name} 已存在")
            else:
                cursor.execute(f"CREATE INDEX `{idx_name}` ON `{table}` (`{col}`)")
                conn.commit()
                print(f"  [OK] 已创建索引 {idx_name}")
        except Exception as e:
            print(f"  [WARN] 索引 {idx_name}: {e}")
            conn.rollback()

    # 创建默认admin账号
    try:
        cursor.execute("SELECT COUNT(*) FROM users WHERE username='admin'")
        count = cursor.fetchone()[0]
        if count == 0:
            import hashlib
            pwd_hash = hashlib.sha256('admin123'.encode('utf-8')).hexdigest()
            cursor.execute(
                "INSERT INTO users (username, password_hash, role, enabled) VALUES (%s, %s, %s, %s)",
                ('admin', pwd_hash, 'admin', 1)
            )
            conn.commit()
            print("  [OK] 已创建默认admin账号 (admin / admin123)")
        else:
            print("  [SKIP] admin账号已存在")
    except Exception as e:
        print(f"  [WARN] 创建admin账号: {e}")
        conn.rollback()

    cursor.close()
    conn.close()
    print("\n数据库迁移完成!")


if __name__ == '__main__':
    run_migration()
