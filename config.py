import os
import random

# ============ 数据库配置 ============
DB_HOST = os.environ.get("DB_HOST", "47.95.157.46")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "facebook_monitor")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASS = os.environ.get("DB_PASS", "root@kunkun")
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

# ============ Flask配置 ============
FLASK_PORT = 6000
FLASK_DEBUG = False

# ============ Cookie配置 ============
COOKIE_URL = "https://ceshi-1300392622.cos.ap-beijing.myqcloud.com/facebook-cookies/kewen789456%40gmail.com.json"
COOKIES_DIR = os.path.join(os.path.expanduser("~"), "fb_cookies")

# ============ AI配置 ============
ZHIPU_KEY_API = "http://47.95.157.46:8520/api/zhipuai_key"
ZHIPU_MODEL = "glm-z1-flash"

# ============ 监控页面配置 ============
MONITOR_PAGES = [
    {
        "name": "home",
        "label": "首页动态",
        "url": "https://www.facebook.com/",
        "refresh_type": "home_button",  # 点击首页按钮刷新
    },
    {
        "name": "groups",
        "label": "小组动态",
        "url": "https://www.facebook.com/?filter=groups&sk=h_chr",
        "refresh_type": "groups_link",  # 点击小组链接刷新
    },
    {
        "name": "search",
        "label": "搜索帖子",
        "url": "https://www.facebook.com/search/posts?q=Looking&filters=eyJyZWNlbnRfcG9zdHM6MCI6IntcIm5hbWVcIjpcInJlY2VudF9wb3N0c1wiLFwiYXJnc1wiOlwiXCJ9IiwicnBfY3JlYXRpb25fdGltZTowIjoie1wibmFtZVwiOlwiY3JlYXRpb25fdGltZVwiLFwiYXJnc1wiOlwie1xcXCJzdGFydF95ZWFyXFxcIjpcXFwiMjAyNlxcXCIsXFxcInN0YXJ0X21vbnRoXFxcIjpcXFwiMjAyNi0xXFxcIixcXFwiZW5kX3llYXJcXFwiOlxcXCIyMDI2XFxcIixcXFwiZW5kX21vbnRoXFxcIjpcXFwiMjAyNi0xMlxcXCIsXFxcInN0YXJ0X2RheVxcXCI6XFxcIjIwMjYtMS0xXFxcIixcXFwiZW5kX2RheVxcXCI6XFxcIjIwMjYtMTItMzFcXFwifVwifSJ9",
        "refresh_type": "search_refilter",  # 重新筛选刷新
    },
]

# ============ 监控参数 ============
MAX_POSTS_PER_PAGE = 100  # 每个页面最多刷100个帖子
INTEREST_PROBABILITY = 0.8  # 80%概率点击有兴趣/没兴趣
LIKE_PROBABILITY = 0.3  # 30%概率点赞
SCROLL_WAIT_MIN = 2  # 滚动后最小等待时间(秒)
SCROLL_WAIT_MAX = 5  # 滚动后最大等待时间(秒)
ACTION_WAIT_MIN = 1  # 操作间最小等待时间(秒)
ACTION_WAIT_MAX = 3  # 操作间最大等待时间(秒)
POST_LOAD_TIMEOUT = 15  # 帖子加载超时(秒)
INTEREST_CLICK_WAIT = 3  # 点击有兴趣后等待时间(秒)

# ============ 反检测配置 ============
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

USER_DATA_DIR = os.path.join(os.path.expanduser("~"), "fb_monitor_profile")


def random_delay(min_s=None, max_s=None):
    """随机延迟"""
    import time
    min_s = min_s or ACTION_WAIT_MIN
    max_s = max_s or ACTION_WAIT_MAX
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)
    return delay
