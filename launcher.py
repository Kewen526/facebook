"""
Facebook监控系统 - 图形化启动器
打包成exe后，用户双击即可运行，无需Python环境
使用tkinter构建桌面GUI界面
"""
import os
import sys
import time
import threading
import webbrowser
import logging
import json

# ===== PyInstaller 打包依赖声明 =====
# 以下导入仅用于让 PyInstaller 正确检测并打包所有依赖
# 不要删除这些导入，即使IDE提示"未使用"
try:
    import sqlalchemy
    import sqlalchemy.dialects.mysql
    import pymysql
    import flask
    import flask_socketio
    import requests
    import selenium
    import selenium.webdriver
    import selenium.webdriver.chrome
    import selenium.webdriver.chrome.service
    import selenium.webdriver.chrome.options
    import selenium.webdriver.common.by
    import selenium.webdriver.common.keys
    import selenium.webdriver.support.ui
    import selenium.webdriver.support.expected_conditions
    import engineio.async_drivers.threading
except ImportError as _e:
    pass  # 运行时实际导入由各模块自行处理
# ===== 依赖声明结束 =====

# 兼容PyInstaller打包
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# 切换到项目目录（必须在import项目模块之前）
os.chdir(get_base_path())

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LogHandler(logging.Handler):
    """将日志输出到tkinter Text组件"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record) + '\n'
        try:
            self.text_widget.after(0, self._append, msg)
        except Exception:
            pass

    def _append(self, msg):
        try:
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, msg)
            self.text_widget.see(tk.END)
            self.text_widget.configure(state='disabled')
        except Exception:
            pass


class FacebookMonitorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Facebook 帖子监控系统")
        self.root.geometry("700x580")
        self.root.resizable(True, True)
        self.root.configure(bg='#f0f2f5')

        # 状态
        self.server_running = False
        self.flask_thread = None
        self.logged_in = False
        self.current_user = None
        self.port = 8080

        # 设置图标（如果有的话）
        try:
            self.root.iconbitmap('icon.ico')
        except Exception:
            pass

        # 设置样式
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self._setup_styles()

        # 构建界面
        self._build_login_frame()
        self._build_main_frame()

        # 初始显示登录界面
        self.show_login()

        # 关闭窗口时清理
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_styles(self):
        self.style.configure('Title.TLabel', font=('Microsoft YaHei', 18, 'bold'),
                             background='#f0f2f5', foreground='#1877f2')
        self.style.configure('SubTitle.TLabel', font=('Microsoft YaHei', 10),
                             background='#f0f2f5', foreground='#65676b')
        self.style.configure('Status.TLabel', font=('Microsoft YaHei', 10),
                             background='#f0f2f5')
        self.style.configure('Big.TButton', font=('Microsoft YaHei', 11), padding=8)
        self.style.configure('Green.TButton', font=('Microsoft YaHei', 11, 'bold'), padding=8)
        self.style.configure('Red.TButton', font=('Microsoft YaHei', 11, 'bold'), padding=8)

    # ========== 登录界面 ==========
    def _build_login_frame(self):
        self.login_frame = tk.Frame(self.root, bg='#f0f2f5')

        # 居中容器
        center = tk.Frame(self.login_frame, bg='white', padx=40, pady=30,
                          highlightbackground='#dddfe2', highlightthickness=1)
        center.place(relx=0.5, rely=0.45, anchor='center')

        tk.Label(center, text="Facebook 监控系统", font=('Microsoft YaHei', 20, 'bold'),
                 fg='#1877f2', bg='white').pack(pady=(0, 5))
        tk.Label(center, text="登录后即可使用", font=('Microsoft YaHei', 10),
                 fg='#65676b', bg='white').pack(pady=(0, 20))

        # 用户名
        tk.Label(center, text="用户名", font=('Microsoft YaHei', 10),
                 fg='#333', bg='white', anchor='w').pack(fill='x')
        self.entry_username = tk.Entry(center, font=('Microsoft YaHei', 12),
                                        width=30, relief='solid', bd=1)
        self.entry_username.pack(pady=(2, 10), ipady=6)

        # 密码
        tk.Label(center, text="密码", font=('Microsoft YaHei', 10),
                 fg='#333', bg='white', anchor='w').pack(fill='x')
        self.entry_password = tk.Entry(center, font=('Microsoft YaHei', 12),
                                        width=30, show='*', relief='solid', bd=1)
        self.entry_password.pack(pady=(2, 15), ipady=6)
        self.entry_password.bind('<Return>', lambda e: self.do_login())

        # 登录按钮
        self.btn_login = tk.Button(center, text="登  录", font=('Microsoft YaHei', 13, 'bold'),
                                    bg='#1877f2', fg='white', relief='flat',
                                    cursor='hand2', command=self.do_login,
                                    width=25, height=1)
        self.btn_login.pack(pady=(5, 10), ipady=4)

        # 错误提示
        self.login_error = tk.Label(center, text="", font=('Microsoft YaHei', 9),
                                     fg='#fa3e3e', bg='white')
        self.login_error.pack()

    # ========== 主界面 ==========
    def _build_main_frame(self):
        self.main_frame = tk.Frame(self.root, bg='#f0f2f5')

        # 顶部信息栏
        top_bar = tk.Frame(self.main_frame, bg='#1877f2', height=50)
        top_bar.pack(fill='x')
        top_bar.pack_propagate(False)

        tk.Label(top_bar, text="Facebook 监控系统", font=('Microsoft YaHei', 14, 'bold'),
                 fg='white', bg='#1877f2').pack(side='left', padx=15)

        self.lbl_user_info = tk.Label(top_bar, text="", font=('Microsoft YaHei', 10),
                                       fg='white', bg='#1877f2')
        self.lbl_user_info.pack(side='right', padx=15)

        btn_logout = tk.Button(top_bar, text="退出登录", font=('Microsoft YaHei', 9),
                                bg='#166fe5', fg='white', relief='flat',
                                cursor='hand2', command=self.do_logout)
        btn_logout.pack(side='right', padx=5)

        # 控制面板区域
        control_panel = tk.LabelFrame(self.main_frame, text=" 控制面板 ",
                                       font=('Microsoft YaHei', 11, 'bold'),
                                       bg='white', fg='#333', padx=15, pady=12)
        control_panel.pack(fill='x', padx=12, pady=(12, 6))

        # 服务状态行
        row1 = tk.Frame(control_panel, bg='white')
        row1.pack(fill='x', pady=(0, 10))

        tk.Label(row1, text="服务状态:", font=('Microsoft YaHei', 10),
                 bg='white', fg='#333').pack(side='left')
        self.lbl_server_status = tk.Label(row1, text="● 未启动",
                                           font=('Microsoft YaHei', 10, 'bold'),
                                           bg='white', fg='#fa3e3e')
        self.lbl_server_status.pack(side='left', padx=(5, 20))

        tk.Label(row1, text=f"端口: {self.port}", font=('Microsoft YaHei', 9),
                 bg='white', fg='#65676b').pack(side='left')

        # 浏览器显示选项
        row2 = tk.Frame(control_panel, bg='white')
        row2.pack(fill='x', pady=(0, 10))

        tk.Label(row2, text="浏览器窗口:", font=('Microsoft YaHei', 10),
                 bg='white', fg='#333').pack(side='left')

        self.browser_mode = tk.StringVar(value='show')
        rb_show = tk.Radiobutton(row2, text="显示浏览器窗口", variable=self.browser_mode,
                                  value='show', font=('Microsoft YaHei', 10),
                                  bg='white', fg='#333', selectcolor='white',
                                  command=self.on_browser_mode_change)
        rb_show.pack(side='left', padx=(10, 5))

        rb_hide = tk.Radiobutton(row2, text="隐藏浏览器窗口（后台运行）", variable=self.browser_mode,
                                  value='hide', font=('Microsoft YaHei', 10),
                                  bg='white', fg='#333', selectcolor='white',
                                  command=self.on_browser_mode_change)
        rb_hide.pack(side='left', padx=5)

        # 操作按钮行
        row3 = tk.Frame(control_panel, bg='white')
        row3.pack(fill='x', pady=(5, 0))

        self.btn_start_server = tk.Button(row3, text="▶ 启动服务",
                                           font=('Microsoft YaHei', 11, 'bold'),
                                           bg='#42b72a', fg='white', relief='flat',
                                           cursor='hand2', width=14, height=1,
                                           command=self.start_server)
        self.btn_start_server.pack(side='left', padx=(0, 8), ipady=3)

        self.btn_stop_server = tk.Button(row3, text="■ 停止服务",
                                          font=('Microsoft YaHei', 11, 'bold'),
                                          bg='#cccccc', fg='white', relief='flat',
                                          width=14, height=1, state='disabled',
                                          command=self.stop_server)
        self.btn_stop_server.pack(side='left', padx=(0, 8), ipady=3)

        self.btn_open_web = tk.Button(row3, text="🌐 打开管理页面",
                                       font=('Microsoft YaHei', 10),
                                       bg='#1877f2', fg='white', relief='flat',
                                       cursor='hand2', width=16, state='disabled',
                                       command=self.open_web_panel)
        self.btn_open_web.pack(side='left', padx=(0, 8), ipady=3)

        # 日志区域
        log_frame = tk.LabelFrame(self.main_frame, text=" 运行日志 ",
                                   font=('Microsoft YaHei', 11, 'bold'),
                                   bg='white', fg='#333', padx=10, pady=8)
        log_frame.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        self.log_text = scrolledtext.ScrolledText(log_frame, font=('Consolas', 9),
                                                    bg='#1e1e1e', fg='#d4d4d4',
                                                    insertbackground='white',
                                                    state='disabled', wrap='word',
                                                    height=12)
        self.log_text.pack(fill='both', expand=True)

        # 注册日志handler
        log_handler = LogHandler(self.log_text)
        log_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                                                     datefmt='%H:%M:%S'))
        logging.getLogger().addHandler(log_handler)

    # ========== 界面切换 ==========
    def show_login(self):
        self.main_frame.pack_forget()
        self.login_frame.pack(fill='both', expand=True)
        self.entry_username.focus_set()

    def show_main(self):
        self.login_frame.pack_forget()
        self.main_frame.pack(fill='both', expand=True)

    # ========== 登录逻辑 ==========
    def do_login(self):
        username = self.entry_username.get().strip()
        password = self.entry_password.get().strip()

        if not username or not password:
            self.login_error.config(text="请输入用户名和密码")
            return

        self.btn_login.config(state='disabled', text="登录中...")
        self.login_error.config(text="")
        self.root.update()

        # 先确保服务器已启动再登录验证
        # 直接用数据库验证（不需要服务器启动）
        try:
            from models import get_session, User, init_db
            init_db()
            session = get_session()
            try:
                user = session.query(User).filter(User.username == username).first()
                if not user or not user.check_password(password):
                    self.login_error.config(text="用户名或密码错误")
                    self.btn_login.config(state='normal', text="登  录")
                    return
                if not user.enabled:
                    self.login_error.config(text="账号已被禁用")
                    self.btn_login.config(state='normal', text="登  录")
                    return

                self.logged_in = True
                self.current_user = {
                    'id': user.id,
                    'username': user.username,
                    'role': user.role,
                }
                role_map = {'admin': '管理员', 'manager': '经理', 'employee': '员工'}
                self.lbl_user_info.config(
                    text=f"当前用户: {user.username} ({role_map.get(user.role, user.role)})")
                self.show_main()
                logger.info(f"用户 {user.username} 登录成功")
            finally:
                session.close()
        except Exception as e:
            self.login_error.config(text=f"登录失败: {e}")

        self.btn_login.config(state='normal', text="登  录")

    def do_logout(self):
        if self.server_running:
            if not messagebox.askyesno("确认", "服务正在运行中，退出登录会停止服务。\n确定要退出吗？"):
                return
            self.stop_server()

        self.logged_in = False
        self.current_user = None
        self.entry_password.delete(0, tk.END)
        self.login_error.config(text="")
        self.show_login()

    # ========== 浏览器模式 ==========
    def on_browser_mode_change(self):
        import config
        headless = self.browser_mode.get() == 'hide'
        config.BROWSER_HEADLESS = headless
        mode = "隐藏" if headless else "显示"
        logger.info(f"浏览器窗口设置为: {mode}（下次启动浏览器实例时生效）")

    # ========== 服务控制 ==========
    def start_server(self):
        if self.server_running:
            return

        self.btn_start_server.config(state='disabled', bg='#cccccc')
        self.root.update()

        # 应用headless设置
        self.on_browser_mode_change()

        def _run_flask():
            try:
                from models import init_db
                from app import app, start_auto_translate

                init_db()
                start_auto_translate()

                logger.info(f"正在启动Web服务，端口: {self.port}")
                logger.info(f"访问 http://localhost:{self.port}")

                # 更新UI状态
                self.root.after(0, self._on_server_started)

                app.run(host='0.0.0.0', port=self.port, debug=False, threaded=True,
                        use_reloader=False)
            except Exception as e:
                logger.error(f"服务启动失败: {e}")
                self.root.after(0, self._on_server_stopped)

        self.flask_thread = threading.Thread(target=_run_flask, daemon=True)
        self.flask_thread.start()

    def _on_server_started(self):
        self.server_running = True
        self.lbl_server_status.config(text="● 运行中", fg='#42b72a')
        self.btn_start_server.config(state='disabled', bg='#cccccc')
        self.btn_stop_server.config(state='normal', bg='#fa3e3e', cursor='hand2')
        self.btn_open_web.config(state='normal', cursor='hand2')

    def _on_server_stopped(self):
        self.server_running = False
        self.lbl_server_status.config(text="● 已停止", fg='#fa3e3e')
        self.btn_start_server.config(state='normal', bg='#42b72a', cursor='hand2')
        self.btn_stop_server.config(state='disabled', bg='#cccccc', cursor='arrow')
        self.btn_open_web.config(state='disabled', cursor='arrow')

    def stop_server(self):
        if not self.server_running:
            return
        logger.info("正在停止服务...")
        try:
            # 停止监控和发送
            try:
                from monitor import stop_monitor
                stop_monitor()
            except Exception:
                pass
            try:
                from task_queue import stop_task_processor
                stop_task_processor()
            except Exception:
                pass

            # Flask在daemon线程中，主程序退出时自动停止
            self._on_server_stopped()
            logger.info("服务已停止（浏览器实例需手动关闭或等待自动关闭）")
        except Exception as e:
            logger.error(f"停止服务出错: {e}")

    def open_web_panel(self):
        url = f"http://localhost:{self.port}"
        webbrowser.open(url)
        logger.info(f"已打开管理页面: {url}")

    # ========== 退出 ==========
    def on_close(self):
        if self.server_running:
            if not messagebox.askyesno("确认退出", "服务正在运行中，关闭窗口会停止所有任务。\n确定要退出吗？"):
                return
            self.stop_server()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = FacebookMonitorApp()
    app.run()


if __name__ == '__main__':
    main()
