"""
Facebook帖子监控 - 本地独立运行脚本
在本地PyCharm中直接运行此文件，自动抓取Facebook帖子并写入数据库
服务器面板 http://47.95.157.46:8080 自动展示数据
"""
import sys
import logging
from models import init_db
from monitor import start_monitor

# 配置日志 - 直接输出到控制台
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
# 强制刷新输出
sys.stdout.reconfigure(line_buffering=True)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Facebook帖子监控系统 - 本地启动")
    logger.info("数据将写入远程MySQL数据库")
    logger.info("服务器面板: http://47.95.157.46:8080")
    logger.info("=" * 60)

    # 初始化数据库表
    init_db()

    # 直接运行监控（不通过Flask线程）
    logger.info("开始监控...")
    try:
        start_monitor()
    except KeyboardInterrupt:
        logger.info("监控已被用户停止 (Ctrl+C)")
    except Exception as e:
        logger.error(f"监控出错: {e}", exc_info=True)
