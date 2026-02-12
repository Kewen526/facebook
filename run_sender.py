"""
Facebook发送引擎 - 本地独立运行脚本
在本地PyCharm中直接运行此文件，自动执行评论、私信、加好友任务
服务器面板 http://47.95.157.46:8080 自动展示数据
"""
import sys
import logging
from models import init_db
from task_queue import run_task_processor

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
    logger.info("Facebook发送引擎 - 本地启动")
    logger.info("每个发送账号独立线程 + 独立浏览器")
    logger.info("自动执行: 评论 / 私信 / 加好友")
    logger.info("数据将写入远程MySQL数据库")
    logger.info("服务器面板: http://47.95.157.46:8080")
    logger.info("=" * 60)

    # 初始化数据库表
    init_db()

    # 直接运行发送任务处理器（不通过Flask线程）
    logger.info("发送引擎启动中...")
    try:
        run_task_processor()
    except KeyboardInterrupt:
        logger.info("发送引擎已被用户停止 (Ctrl+C)")
    except Exception as e:
        logger.error(f"发送引擎出错: {e}", exc_info=True)
