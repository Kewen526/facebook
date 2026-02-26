import os
import json
import random
import base64
from datetime import datetime as _dt
from urllib.parse import quote_plus as _qp

# ============ 数据库配置 ============
DB_HOST = os.environ.get("DB_HOST", "47.95.157.46")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "facebook_monitor")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASS = os.environ.get("DB_PASS", "root@kunkun")
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{_qp(DB_PASS)}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

# ============ Flask配置 ============
FLASK_PORT = 8080
FLASK_DEBUG = False

# ============ Cookie配置 ============
COOKIE_URL = "https://ceshi-1300392622.cos.ap-beijing.myqcloud.com/facebook-cookies/kewen789456%40gmail.com.json"
COOKIES_DIR = os.path.join(os.path.expanduser("~"), "fb_cookies")

# ============ AI配置 ============
ZHIPU_KEY_API = "http://47.95.157.46:8520/api/zhipuai_key"
ZHIPU_MODEL = "glm-4.7-flash"

# ============ 百度翻译配置 ============
BAIDU_TRANSLATE_APPID = "20230724001755506"
BAIDU_TRANSLATE_SECRET = "JAXZq_RAOCOPY3Af440n"
BAIDU_TRANSLATE_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"

# ============ 发送配置 ============
SEND_COOLDOWN_SECONDS = 600  # 每个发送账号任务间隔10分钟
DAILY_SEND_LIMIT = 4  # 每个发送账号每天最多执行的发送任务数
SEARCH_KEYWORD = "dropshipping"  # 搜索关键词


def build_fb_today_url(keyword=None):
    """生成今天的Facebook搜索URL"""
    keyword = keyword or SEARCH_KEYWORD
    today = _dt.now()
    time_args = {
        "start_year": str(today.year),
        "start_month": f"{today.year}-{today.month}",
        "end_year": str(today.year),
        "end_month": f"{today.year}-{today.month}",
        "start_day": f"{today.year}-{today.month}-{today.day}",
        "end_day": f"{today.year}-{today.month}-{today.day}"
    }
    filters = {
        "recent_posts:0": json.dumps({"name": "recent_posts", "args": ""}),
        "rp_creation_time:0": json.dumps({"name": "creation_time", "args": json.dumps(time_args)})
    }
    filters_b64 = base64.b64encode(json.dumps(filters, separators=(',', ':')).encode()).decode()
    return f"https://www.facebook.com/search/posts?q={keyword}&filters={filters_b64}"


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
]

# ============ 监控参数 ============
MAX_POSTS_PER_PAGE = 100  # 每个页面最多刷100个帖子
INTEREST_PROBABILITY = 1.0  # 目标帖子100%点击有兴趣（必须执行）
NOT_INTERESTED_PROBABILITY = 0.15  # 非目标帖子15%概率点击没兴趣（降低避免风控）
LIKE_PROBABILITY = 0.002  # 0.2%概率点赞（进一步降低）
NOT_INTERESTED_DELAY_MIN = 3  # 点击没兴趣前最小延迟(秒)，降低速度避免风控
NOT_INTERESTED_DELAY_MAX = 6  # 点击没兴趣前最大延迟(秒)
SCROLL_WAIT_MIN = 1  # 滚动后最小等待时间(秒)
SCROLL_WAIT_MAX = 3  # 滚动后最大等待时间(秒)
ACTION_WAIT_MIN = 0.5  # 操作间最小等待时间(秒)
ACTION_WAIT_MAX = 2  # 操作间最大等待时间(秒)
POST_LOAD_TIMEOUT = 15  # 帖子加载超时(秒)
INTEREST_CLICK_WAIT = 1.5  # 点击有兴趣后等待时间(秒)
AI_BATCH_SIZE = 10  # 批量AI分析帖子数量
AI_CONCURRENT_VOTES = 3  # 并发投票数
ROUND_INTERVAL_MIN = 5  # 每轮监控间隔最小(秒)
ROUND_INTERVAL_MAX = 10  # 每轮监控间隔最大(秒)

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
