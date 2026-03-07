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
    # 显示当前Python信息
    print(f"当前 Python: {sys.executable}")
    print(f"Python 版本: {sys.version}")
    print()

    # 确保PyInstaller已安装（在当前Python环境中）
    try:
        import PyInstaller
        print(f"PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

    # 检查关键依赖是否已安装
    missing = []
    for mod_name in ['sqlalchemy', 'pymysql', 'flask', 'flask_socketio', 'requests', 'selenium', 'jinja2']:
        try:
            __import__(mod_name)
            mod = sys.modules[mod_name]
            print(f"  [OK] {mod_name}: {getattr(mod, '__version__', '?')} -> {getattr(mod, '__file__', '?')}")
        except ImportError:
            missing.append(mod_name)
            print(f"  [MISSING] {mod_name}")
    if missing:
        print(f"\n错误: 以下依赖未安装: {', '.join(missing)}")
        print(f"请先运行: {sys.executable} -m pip install {' '.join(missing)}")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 查找 VC++ 运行库 DLL，打包进去以便目标电脑无需额外安装
    vc_dlls = []
    python_dir = os.path.dirname(sys.executable)
    for dll_name in ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll']:
        # 优先从 Python 安装目录查找
        dll_path = os.path.join(python_dir, dll_name)
        if not os.path.exists(dll_path):
            # 也在 System32 中查找
            dll_path = os.path.join(os.environ.get('SYSTEMROOT', r'C:\Windows'), 'System32', dll_name)
        if os.path.exists(dll_path):
            vc_dlls.append(dll_path)
            print(f"  [DLL] 找到 {dll_name}: {dll_path}")
        else:
            print(f"  [DLL] 未找到 {dll_name}（可能不影响）")

    # 使用 sys.executable -m PyInstaller 确保用当前Python环境的PyInstaller
    # 这样可以避免多个Python版本共存时调用错误版本的问题
    args = [
        sys.executable, '-m', 'PyInstaller',
        '--name=FacebookMonitor',
        '--onedir',           # 打包为目录（比onefile启动更快）
        '--windowed',         # GUI模式，不显示黑色控制台窗口
        '--noconfirm',        # 覆盖已有输出
    ]

    # 将 VC++ 运行库 DLL 添加为二进制文件
    for dll_path in vc_dlls:
        args.append(f'--add-binary={dll_path}' + os.pathsep + '.')

    # 同时添加项目目录本身
    args.append(f'--paths={base_dir}')

    args += [
        # 添加数据文件：模板和所有项目Python文件
        f'--add-data={os.path.join(base_dir, "templates")}' + os.pathsep + 'templates',
        f'--add-data={os.path.join(base_dir, "config.py")}' + os.pathsep + '.',
        f'--add-data={os.path.join(base_dir, "models.py")}' + os.pathsep + '.',
        f'--add-data={os.path.join(base_dir, "app.py")}' + os.pathsep + '.',
        f'--add-data={os.path.join(base_dir, "monitor.py")}' + os.pathsep + '.',
        f'--add-data={os.path.join(base_dir, "sender.py")}' + os.pathsep + '.',
        f'--add-data={os.path.join(base_dir, "task_queue.py")}' + os.pathsep + '.',
        f'--add-data={os.path.join(base_dir, "ai_analyzer.py")}' + os.pathsep + '.',

        # 强制收集完整包（确保所有子模块都被打包）
        '--collect-all=sqlalchemy',
        '--collect-all=pymysql',
        '--collect-all=flask',
        '--collect-all=flask_socketio',
        '--collect-all=jinja2',
        '--collect-all=markupsafe',
        '--collect-all=engineio',

        # 隐式导入（PyInstaller可能检测不到的模块）
        '--hidden-import=sqlalchemy',
        '--hidden-import=sqlalchemy.orm',
        '--hidden-import=sqlalchemy.pool',
        '--hidden-import=sqlalchemy.engine',
        '--hidden-import=sqlalchemy.dialects.mysql',
        '--hidden-import=sqlalchemy.dialects.mysql.pymysql',
        '--hidden-import=pymysql',
        '--hidden-import=pymysql.cursors',
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

        # 排除不需要的大型包（减小体积，但保留tkinter）
        '--exclude-module=matplotlib',
        '--exclude-module=numpy',
        '--exclude-module=scipy',
        '--exclude-module=pandas',

        # 入口文件
        os.path.join(base_dir, 'launcher.py'),
    ]

    print("=" * 50)
    print("开始打包 Facebook 监控系统（GUI版本）")
    print("=" * 50)
    print(f"入口: launcher.py (tkinter GUI)")
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
        print(f"  3. 弹出图形化登录窗口")
        print(f"  4. 登录后可以:")
        print(f"     - 选择是否显示浏览器窗口")
        print(f"     - 点击「启动服务」开始监控")
        print(f"     - 点击「打开管理页面」在浏览器中查看")
        print()
        print("注意: 用户电脑需要安装 Chrome 浏览器")
        print("=" * 50)
    else:
        print()
        print("打包失败，请检查错误信息")
        sys.exit(1)


if __name__ == '__main__':
    build()
