"""
PyInstaller 打包脚本
用法: python build_exe.py

前提条件:
  pip install pyinstaller

打包后的exe位于 dist/FacebookMonitor/ 目录下
"""
import os
import sys
import subprocess


def build():
    # 确保PyInstaller已安装
    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # PyInstaller参数
    args = [
        'pyinstaller',
        '--name=FacebookMonitor',
        '--onedir',           # 打包为目录（比onefile启动更快）
        '--console',          # 显示控制台窗口（方便查看日志）
        '--noconfirm',        # 覆盖已有输出

        # 添加数据文件
        f'--add-data={os.path.join(base_dir, "templates")}' + os.pathsep + 'templates',
        f'--add-data={os.path.join(base_dir, "config.py")}' + os.pathsep + '.',

        # 隐式导入（PyInstaller可能检测不到的模块）
        '--hidden-import=sqlalchemy.dialects.mysql',
        '--hidden-import=pymysql',
        '--hidden-import=flask_socketio',
        '--hidden-import=eventlet',
        '--hidden-import=engineio.async_drivers.threading',
        '--hidden-import=selenium',
        '--hidden-import=selenium.webdriver',
        '--hidden-import=selenium.webdriver.chrome',
        '--hidden-import=selenium.webdriver.chrome.service',
        '--hidden-import=selenium.webdriver.chrome.options',
        '--hidden-import=selenium.webdriver.common.by',
        '--hidden-import=selenium.webdriver.support.ui',
        '--hidden-import=selenium.webdriver.support.expected_conditions',

        # 排除不需要的大型包（减小体积）
        '--exclude-module=tkinter',
        '--exclude-module=matplotlib',
        '--exclude-module=numpy',
        '--exclude-module=scipy',
        '--exclude-module=pandas',

        # 入口文件
        os.path.join(base_dir, 'launcher.py'),
    ]

    print("=" * 50)
    print("开始打包 Facebook 监控系统")
    print("=" * 50)
    print(f"入口: launcher.py")
    print(f"输出: dist/FacebookMonitor/")
    print()

    result = subprocess.run(args, cwd=base_dir)

    if result.returncode == 0:
        dist_path = os.path.join(base_dir, 'dist', 'FacebookMonitor')
        print()
        print("=" * 50)
        print("打包成功!")
        print(f"输出目录: {dist_path}")
        print()
        print("使用方法:")
        print(f"  1. 进入 {dist_path}")
        print(f"  2. 双击 FacebookMonitor.exe 启动")
        print(f"  3. 浏览器会自动打开登录页面")
        print()
        print("命令行参数:")
        print("  FacebookMonitor.exe --no-browser  # 不自动打开浏览器")
        print("  FacebookMonitor.exe --port 9090   # 使用自定义端口")
        print("=" * 50)
    else:
        print()
        print("打包失败，请检查错误信息")
        sys.exit(1)


if __name__ == '__main__':
    build()
