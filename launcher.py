"""
Facebook监控系统启动器
打包成exe后，用户双击即可运行，无需Python环境
"""
import os
import sys
import time
import argparse
import webbrowser
import threading
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_base_path():
    """获取资源文件的基础路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def open_browser(port, delay=2):
    """延迟打开浏览器"""
    time.sleep(delay)
    url = f"http://localhost:{port}"
    logger.info(f"正在打开浏览器: {url}")
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description='Facebook监控系统启动器')
    parser.add_argument('--no-browser', action='store_true',
                        help='启动时不自动打开浏览器')
    parser.add_argument('--port', type=int, default=8080,
                        help='服务端口号 (默认: 8080)')
    args = parser.parse_args()

    # 切换到项目目录
    base_path = get_base_path()
    os.chdir(base_path)

    # 设置端口
    from config import FLASK_PORT
    port = args.port or FLASK_PORT

    logger.info("=" * 50)
    logger.info("Facebook 帖子监控系统")
    logger.info("=" * 50)
    logger.info(f"服务地址: http://localhost:{port}")
    if not args.no_browser:
        logger.info("浏览器将自动打开...")
    logger.info("按 Ctrl+C 停止服务")
    logger.info("=" * 50)

    # 初始化数据库
    from models import init_db
    init_db()

    # 启动自动翻译
    from app import start_auto_translate, app
    start_auto_translate()

    # 自动打开浏览器
    if not args.no_browser:
        browser_thread = threading.Thread(
            target=open_browser, args=(port,), daemon=True
        )
        browser_thread.start()

    # 启动Flask服务
    try:
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("\n服务已停止")
    except Exception as e:
        logger.error(f"启动失败: {e}")
        input("按回车键退出...")


if __name__ == '__main__':
    main()
