import os
import re
import json
import time
import random
import logging
import requests
import threading
from datetime import datetime, timezone

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains

from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    COOKIE_URL, COOKIES_DIR, USER_AGENTS, USER_DATA_DIR,
    MONITOR_PAGES, MAX_POSTS_PER_PAGE,
    INTEREST_PROBABILITY, NOT_INTERESTED_PROBABILITY, LIKE_PROBABILITY,
    NOT_INTERESTED_DELAY_MIN, NOT_INTERESTED_DELAY_MAX,
    SCROLL_WAIT_MIN, SCROLL_WAIT_MAX,
    ACTION_WAIT_MIN, ACTION_WAIT_MAX,
    POST_LOAD_TIMEOUT, INTEREST_CLICK_WAIT,
    AI_BATCH_SIZE, ROUND_INTERVAL_MIN, ROUND_INTERVAL_MAX,
    random_delay,
)
from models import is_post_exists, save_post, update_post_action, MonitorLog, get_session, Account
from ai_analyzer import analyze_post_concurrent

logger = logging.getLogger(__name__)

# 全局状态 - 供Flask API查询（支持多账号）
monitor_status = {
    "running": False,
    "current_page": "",
    "current_page_label": "",
    "posts_processed": 0,
    "posts_total": 0,
    "round_count": 0,
    "last_post_content": "",
    "last_action": "",
    "error": "",
    "accounts": {},  # 每个监控账号的状态
}

# 跟踪每个监控线程
_monitor_threads = {}
# 被风控的账号 - 停止点赞
_like_disabled_accounts = set()


def update_status(**kwargs):
    """更新监控状态"""
    monitor_status.update(kwargs)


def update_account_status(account_name, **kwargs):
    """更新指定账号的监控状态"""
    if account_name not in monitor_status["accounts"]:
        monitor_status["accounts"][account_name] = {
            "running": False, "current_page": "", "round_count": 0,
            "posts_processed": 0, "last_action": "", "error": "",
        }
    monitor_status["accounts"][account_name].update(kwargs)


def download_cookies(cookie_url=None, account_name=None):
    """从URL下载cookie文件"""
    url = cookie_url or COOKIE_URL
    file_name = f"{account_name}_cookies.json" if account_name else "monitor_cookies.json"
    logger.info(f"下载cookie文件: {url}")
    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)
        cookie_file_path = os.path.join(COOKIES_DIR, file_name)

        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'application/json, text/plain, */*',
        }
        proxies = {'http': None, 'https': None}

        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        if response.status_code != 200:
            logger.error(f"下载失败，HTTP状态码: {response.status_code}")
            return None

        json_data = json.loads(response.text)
        with open(cookie_file_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False)

        logger.info(f"Cookie文件下载成功: {cookie_file_path}")
        return cookie_file_path
    except Exception as e:
        logger.error(f"下载cookie文件出错: {e}")
        return None


def create_driver():
    """创建浏览器实例 - 带反检测措施"""
    print("[Monitor] 创建浏览器实例...", flush=True)
    logger.info("创建浏览器实例...")

    # 先尝试undetected-chromedriver
    try:
        import undetected_chromedriver as uc
        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-infobars")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        user_agent = random.choice(USER_AGENTS)
        options.add_argument(f"--user-agent={user_agent}")
        driver = uc.Chrome(options=options, version_main=None)
        print("[Monitor] 使用undetected-chromedriver创建成功", flush=True)
        logger.info("使用undetected-chromedriver创建成功")
        return driver
    except Exception as e:
        print(f"[Monitor] undetected-chromedriver不可用({e})，使用Selenium+反检测", flush=True)
        logger.info(f"undetected-chromedriver不可用({e})，使用Selenium+反检测")

    # Selenium + 反检测
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        chrome_options = Options()
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-infobars")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")

        # 使用webdriver-manager自动管理ChromeDriver
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            print("[Monitor] 使用webdriver-manager自动安装ChromeDriver成功", flush=True)
            logger.info("使用webdriver-manager自动安装ChromeDriver")
        except ImportError:
            driver = webdriver.Chrome(options=chrome_options)

        # 注入反检测脚本
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                // 隐藏webdriver标识
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                // 模拟真实plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                // 模拟真实语言
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                // 模拟chrome对象
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
                // 隐藏自动化相关属性
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
                // 覆盖permissions查询
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
                );
            '''
        })

        logger.info("使用Selenium+反检测创建浏览器成功")
        return driver
    except Exception as e2:
        logger.error(f"创建浏览器失败: {e2}")
        return None


def load_cookies(driver, cookies_file):
    """加载cookies到浏览器"""
    logger.info(f"加载cookies: {cookies_file}")
    try:
        driver.get("https://www.facebook.com")
        time.sleep(3)

        with open(cookies_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict) and 'cookies' in data:
            cookies = data['cookies']
            local_storage = data.get('local_storage', {})
        elif isinstance(data, list):
            cookies = data
            local_storage = {}
        else:
            logger.error("无法识别的cookies文件格式")
            return False

        for cookie in cookies:
            try:
                cookie_dict = {}
                if 'name' in cookie and 'value' in cookie:
                    cookie_dict['name'] = cookie['name']
                    cookie_dict['value'] = cookie['value']
                    if 'domain' in cookie:
                        cookie_dict['domain'] = cookie['domain']
                    if 'path' in cookie:
                        cookie_dict['path'] = cookie['path']
                    if 'secure' in cookie:
                        cookie_dict['secure'] = cookie['secure']
                    if 'httpOnly' in cookie:
                        cookie_dict['httpOnly'] = cookie['httpOnly']
                    if 'expiry' in cookie:
                        cookie_dict['expiry'] = cookie['expiry']
                    elif 'expires' in cookie and cookie['expires'] is not None:
                        cookie_dict['expiry'] = int(cookie['expires'])
                    driver.add_cookie(cookie_dict)
            except Exception as e:
                pass  # 个别cookie加载失败不影响整体

        if local_storage:
            for key, value in local_storage.items():
                try:
                    driver.execute_script(f"window.localStorage.setItem('{key}', '{value}');")
                except Exception:
                    pass

        driver.refresh()
        time.sleep(4)

        if "Facebook" in driver.title or "facebook" in driver.current_url:
            logger.info("成功登录到Facebook")
            return True
        else:
            logger.warning("登录状态不确定")
            return True
    except Exception as e:
        logger.error(f"加载cookies出错: {e}")
        return False


def open_all_tabs(driver, account_name=None):
    """打开监控页面的标签页（首页+小组）"""
    logger.info("打开监控标签页...")

    # 第一个标签页 - 首页 (当前标签)
    driver.get(MONITOR_PAGES[0]["url"])
    time.sleep(3)
    logger.info(f"标签页1: {MONITOR_PAGES[0]['label']} 已打开")

    # 第二个标签页 - 小组
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[1])
    driver.get(MONITOR_PAGES[1]["url"])
    time.sleep(3)
    logger.info(f"标签页2: {MONITOR_PAGES[1]['label']} 已打开")

    # 切回第一个标签页
    driver.switch_to.window(driver.window_handles[0])
    logger.info("所有标签页已打开，切回首页")


def switch_to_tab(driver, tab_index):
    """切换到指定标签页"""
    handles = driver.window_handles
    if tab_index < len(handles):
        driver.switch_to.window(handles[tab_index])
        time.sleep(1)
        label = MONITOR_PAGES[tab_index]['label'] if tab_index < len(MONITOR_PAGES) else f"标签{tab_index}"
        logger.info(f"已切换到标签页 {tab_index + 1}: {label}")
        return True
    # 只有一个标签页时，tab_index=0直接返回成功
    if tab_index == 0 and len(handles) >= 1:
        driver.switch_to.window(handles[0])
        time.sleep(1)
        return True
    logger.error(f"标签页 {tab_index} 不存在")
    return False


def refresh_page(driver, page_config, tab_index, account_name=None):
    """根据页面类型执行刷新操作"""
    refresh_type = page_config["refresh_type"]
    logger.info(f"刷新页面: {page_config['label']} (类型: {refresh_type})")

    try:
        if refresh_type == "home_button":
            # 首页 - 检测是否已自动刷新，如果没有则点击首页按钮
            # 首页有时会自动刷新，检测方式：看scroll位置
            scroll_pos = driver.execute_script("return window.scrollY;")
            if scroll_pos < 100:
                # 页面已在顶部，可能已自动刷新
                logger.info("首页可能已自动刷新，跳过点击按钮")
                time.sleep(1)
                return True

            # 点击首页按钮 (Home SVG icon)
            try:
                home_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//span[contains(@class,'x1n2onr6')]//svg[contains(@class,'x1lliihq') and @viewBox='0 0 28 28']//path[contains(@d,'M25.825')]/.."
                    ))
                )
                home_btn.click()
                logger.info("已点击首页按钮")
                time.sleep(2)
                return True
            except Exception:
                # 备用方案: 直接导航
                logger.warning("未找到首页按钮，使用导航刷新")
                driver.get(page_config["url"])
                time.sleep(5)
                return True

        elif refresh_type == "groups_link":
            # 小组页面 - 直接导航刷新，确保页面完全重新加载
            logger.info("小组页面: 直接导航刷新")
            driver.get(page_config["url"])
            time.sleep(5)  # 充分等待页面渲染
            return True

    except Exception as e:
        logger.error(f"刷新页面失败: {e}")
        driver.get(page_config["url"])
        time.sleep(3)
        return True


def extract_post_id(post_element):
    """从帖子元素中提取帖子ID"""
    try:
        post_html = post_element.get_attribute('outerHTML')

        # 方法1: multi_permalinks参数
        match = re.search(r'multi_permalinks=(\d+)', post_html)
        if match:
            return match.group(1)

        # 方法2: /posts/ID
        match = re.search(r'/posts/(\d+)', post_html)
        if match:
            return match.group(1)

        # 方法3: story_fbid
        match = re.search(r'story_fbid=(\d+)', post_html)
        if match:
            return match.group(1)

        # 方法4: content_id
        match = re.search(r'content_id["\s:=]+(\d+)', post_html)
        if match:
            return match.group(1)

        # 方法5: 使用data-id或aria属性
        match = re.search(r'data-(?:post-id|story-id|id)="(\d+)"', post_html)
        if match:
            return match.group(1)

        return None
    except Exception as e:
        logger.error(f"提取帖子ID出错: {e}")
        return None


def extract_post_url(post_element):
    """提取帖子链接"""
    try:
        post_html = post_element.get_attribute('outerHTML')

        # 查找permalink
        match = re.search(r'href="(https://www\.facebook\.com/[^"]*(?:posts|permalink)[^"]*)"', post_html)
        if match:
            return match.group(1).replace('&amp;', '&')

        # 查找groups中的帖子链接
        match = re.search(r'href="(https://www\.facebook\.com/groups/[^"]*permalink[^"]*)"', post_html)
        if match:
            return match.group(1).replace('&amp;', '&')

        # 查找story链接
        match = re.search(r'href="(/[^"]*story_fbid[^"]*)"', post_html)
        if match:
            return "https://www.facebook.com" + match.group(1).replace('&amp;', '&')

        return None
    except Exception:
        return None


def _extract_user_id_from_url(href):
    """从Facebook URL中提取用户ID"""
    if not href:
        return ""
    # 方法1: /user/数字ID/ (小组帖子常见)
    match = re.search(r'/user/(\d+)/', href)
    if match:
        return match.group(1)
    # 方法2: profile.php?id=数字ID
    match = re.search(r'profile\.php\?id=(\d+)', href)
    if match:
        return match.group(1)
    # 方法3: facebook.com/用户名 (非数字ID)
    match = re.search(r'facebook\.com/([a-zA-Z0-9.]+)(?:\?|$|/)', href)
    if match:
        val = match.group(1)
        # 排除facebook内部路径
        if val not in ('groups', 'pages', 'profile', 'search', 'watch', 'marketplace', 'events', 'gaming', 'reel'):
            return val
    return ""


def extract_author_info(post_element):
    """提取作者信息"""
    author_name = ""
    author_id = ""
    author_profile_url = ""

    try:
        # 寻找帖子中的作者链接 - 通常是h2/h3下的strong>a或者包含用户名的链接
        try:
            # 方法1: strong标签中的链接
            author_links = post_element.find_elements(By.XPATH, ".//strong//a[contains(@href,'facebook.com')]")
            if author_links:
                link = author_links[0]
                author_name = link.text.strip()
                href = link.get_attribute('href')
                if href:
                    author_profile_url = href.split('?')[0]
                    author_id = _extract_user_id_from_url(href)
        except Exception:
            pass

        if not author_name:
            try:
                # 方法2: h2 or h3中的链接
                header_links = post_element.find_elements(By.XPATH, ".//h2//a | .//h3//a | .//h4//a")
                for link in header_links:
                    href = link.get_attribute('href') or ''
                    if 'facebook.com' in href and '/groups/' not in href:
                        author_name = link.text.strip()
                        author_profile_url = href.split('?')[0]
                        author_id = _extract_user_id_from_url(href)
                        break
            except Exception:
                pass

        # 方法3: 如果还没找到ID，从帖子HTML中搜索/user/数字/模式
        if not author_id:
            try:
                post_html = post_element.get_attribute('outerHTML')
                match = re.search(r'/user/(\d+)/', post_html)
                if match:
                    author_id = match.group(1)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"提取作者信息出错: {e}")

    return author_name, author_id, author_profile_url


def extract_post_time(post_element):
    """提取帖子时间"""
    try:
        # 查找时间相关元素 - 通常是abbr或者带有timestamp的span
        time_elements = post_element.find_elements(By.XPATH,
            ".//a[contains(@href,'/posts/') or contains(@href,'permalink') or contains(@href,'story_fbid')]//span[contains(@class,'x1lliihq')]"
        )
        for elem in time_elements:
            text = elem.text.strip()
            if text and any(kw in text for kw in ['分钟', '小时', '天', 'h', 'm', 'd', 'hr', 'min', 'Just', 'yesterday']):
                return text

        # 备用: 使用aria-label中的时间
        time_elements = post_element.find_elements(By.XPATH, ".//abbr[@data-utime] | .//span[@aria-label]")
        for elem in time_elements:
            label = elem.get_attribute('aria-label') or elem.text
            if label:
                return label.strip()

        # 再备用: 查找所有包含时间特征的文本
        all_spans = post_element.find_elements(By.TAG_NAME, "span")
        for span in all_spans:
            text = span.text.strip()
            if text and re.match(r'^\d+[分小时天hmd]', text):
                return text

        return ""
    except Exception:
        return ""


def get_full_post_content(post_element, driver):
    """获取帖子完整内容 - 仅提取帖子正文部分，排除作者名、时间、点赞评论等"""
    try:
        # 先尝试点击"展开"按钮
        try:
            expand_btns = post_element.find_elements(By.XPATH,
                ".//div[contains(@role,'button') and (contains(text(),'展开') or contains(text(),'See more') or contains(text(),'更多'))]"
                " | .//span[contains(text(),'展开') or contains(text(),'See more')]/.."
            )
            for btn in expand_btns:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    time.sleep(0.3)
                    btn.click()
                    time.sleep(0.8)
                    break
                except Exception:
                    continue
        except Exception:
            pass

        # 精确提取帖子正文 - 查找data-ad-preview="message"的div (Facebook帖子正文容器)
        content = ""

        # 方法1: data-ad-preview="message" 是Facebook帖子正文的标准属性
        try:
            msg_divs = post_element.find_elements(By.XPATH, ".//div[@data-ad-preview='message']")
            if msg_divs:
                content = msg_divs[0].text.strip()
        except Exception:
            pass

        # 方法2: 查找帖子正文区域 - 通常在dir="auto"的div中，位于作者信息之后
        if not content:
            try:
                text_divs = post_element.find_elements(By.XPATH,
                    ".//div[@dir='auto' and @style and contains(@style,'text-align')]"
                )
                texts = []
                for div in text_divs:
                    t = div.text.strip()
                    if t and len(t) > 5:
                        texts.append(t)
                if texts:
                    content = "\n".join(texts)
            except Exception:
                pass

        # 方法3: 查找包含dir="auto"且有实际文本内容的元素（排除按钮文本）
        if not content:
            try:
                auto_divs = post_element.find_elements(By.XPATH,
                    ".//div[@dir='auto'][not(ancestor::div[@role='button'])]"
                    "[not(ancestor::form)]"
                    "[string-length(normalize-space(text())) > 10]"
                )
                texts = []
                seen = set()
                for div in auto_divs:
                    t = div.text.strip()
                    # 排除常见的非正文文本
                    if t and len(t) > 10 and t not in seen:
                        # 排除点赞/评论/分享等计数文本
                        if not re.match(r'^[\d,.]+ ?(likes?|comments?|shares?|赞|条评论|次分享)', t, re.IGNORECASE):
                            seen.add(t)
                            texts.append(t)
                if texts:
                    # 取最长的文本作为正文
                    content = max(texts, key=len)
            except Exception:
                pass

        # 方法4: 最后的回退 - 使用整个帖子文本但尝试清理
        if not content:
            full_text = post_element.text or ""
            # 按行清理，移除明显的非正文内容
            lines = full_text.split('\n')
            body_lines = []
            skip_patterns = [
                r'^(Like|Comment|Share|赞|评论|分享|Reply|回复)$',
                r'^\d+ (likes?|comments?|shares?|赞|条评论|次分享)',
                r'^(All comments|Most relevant|最相关|所有评论)',
                r'^(Write a comment|写评论)',
                r'^\d+[分小时天hmd]',  # 时间戳
                r'^(Just now|Yesterday|昨天)',
            ]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if any(re.match(p, line, re.IGNORECASE) for p in skip_patterns):
                    continue
                body_lines.append(line)
            # 排除第一行（通常是作者名）和最后几行（通常是互动按钮）
            if len(body_lines) > 2:
                content = "\n".join(body_lines[1:-1])
            elif body_lines:
                content = "\n".join(body_lines)

        return content.strip()

    except Exception as e:
        logger.error(f"获取帖子内容出错: {e}")
        return ""


def _is_junk_content(text):
    """检查内容是否是无实质意义的垃圾内容（纯标点/表情/链接等）"""
    if not text:
        return True
    # 移除URL链接
    cleaned = re.sub(r'https?://\S+', '', text)
    # 移除表情符号（emoji）
    cleaned = re.sub(r'[\U00010000-\U0010ffff]', '', cleaned)
    # 移除标点符号和空白
    cleaned = re.sub(r'[\s\.,!?;:·…\-_=+\[\](){}|/\\@#$%^&*~`\'"<>。，！？、；：""''【】（）《》]+', '', cleaned)
    # 如果清理后剩余内容不足10个字符，认为是垃圾内容
    return len(cleaned) < 10


def _is_non_business_content(text):
    """检查内容是否明显与代发业务无关（交友/征婚/社交等）"""
    if not text:
        return False
    text_lower = text.lower()
    # 交友/征婚/社交关键词
    dating_patterns = [
        r'looking for .{0,20}(partner|relationship|love|husband|wife|boyfriend|girlfriend|soulmate|companion)',
        r'(single|divorced).{0,30}(looking|searching|seeking)',
        r'寻找.{0,10}(伴侣|对象|另一半|男友|女友|老公|老婆)',
        r'(dating|hookup|romance|marry|marriage)',
        r'(征婚|相亲|脱单|找对象)',
    ]
    for pattern in dating_patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def _clean_content_for_ai(content, author_name=None):
    """清洗帖子内容：移除小组名称、作者名等非正文信息"""
    if not content:
        return content

    lines = content.split('\n')
    cleaned_lines = []

    # 已知的小组名称/页面标题关键词模式（这些出现在正文开头通常是小组名或用户名）
    group_title_patterns = [
        r'^.{0,60}(代发|代购|Dropshipping|Shopify|采购代理|供应商|sourcing|fulfillment|ecommerce)',
        r'^.{0,60}(1688|阿里巴巴|alibaba|Global Traders)',
    ]

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # 跳过第一行如果匹配小组名/页面标题模式
        if i == 0:
            is_group_title = False
            for pattern in group_title_patterns:
                if re.search(pattern, line_stripped, re.IGNORECASE):
                    is_group_title = True
                    break
            if is_group_title:
                logger.debug(f"移除疑似小组名/标题: {line_stripped[:50]}")
                continue

        # 跳过作者名行
        if author_name and line_stripped == author_name:
            continue

        cleaned_lines.append(line_stripped)

    return '\n'.join(cleaned_lines).strip()


def dismiss_overlay(driver):
    """自动检测并关闭阻挡操作的遮罩层/弹窗"""
    try:
        close_selectors = [
            # 精确匹配用户提供的HTML结构
            "//div[@role='none']//span[contains(@class,'x1lliihq') and (text()='关闭' or text()='Close')]",
            # aria-label方式
            "//div[@aria-label='关闭' or @aria-label='Close'][@role='button']",
            # 通用关闭按钮
            "//div[@role='button']//span[text()='关闭' or text()='Close']",
            "//div[@role='dialog']//div[@aria-label='关闭' or @aria-label='Close']",
        ]
        for selector in close_selectors:
            elements = driver.find_elements(By.XPATH, selector)
            for elem in elements:
                try:
                    if elem.is_displayed():
                        elem.click()
                        time.sleep(1)
                        logger.info("已自动关闭遮罩层/弹窗")
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def click_three_dots_menu(post_element, driver):
    """点击帖子的三个点菜单按钮"""
    # 先检查并关闭可能存在的遮罩层
    dismiss_overlay(driver)
    try:
        # 方法1: 使用aria-label定位 (兼容中英文)
        dots_btn = post_element.find_element(By.XPATH,
            ".//div[@role='button' and ("
            "contains(@aria-label,'操作') or "
            "contains(@aria-label,'Actions') or "
            "contains(@aria-label,'More') or "
            "contains(@aria-label,'更多')"
            ") and @aria-haspopup='menu']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", dots_btn)
        time.sleep(0.3)
        dots_btn.click()
        logger.info("已点击三个点菜单")
        time.sleep(1)
        return True
    except Exception:
        pass

    try:
        # 方法2: 通过SVG三个点图标的path特征定位
        dots_btn = post_element.find_element(By.XPATH,
            ".//div[@role='button' and @aria-haspopup='menu']"
            "[.//svg//path[contains(@d,'M458 360')]]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", dots_btn)
        time.sleep(0.3)
        dots_btn.click()
        logger.info("已点击三个点菜单(SVG方式)")
        time.sleep(1)
        return True
    except Exception:
        pass

    logger.debug("未找到三个点菜单，跳过交互操作")
    return False


def click_interested(driver):
    """点击有兴趣按钮"""
    try:
        interested_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[@role='button']//span[contains(text(),'有兴趣') or contains(text(),'Interested')]"
                "/ancestor::div[@role='button']"
            ))
        )
        interested_btn.click()
        logger.info("已点击有兴趣")
        time.sleep(INTEREST_CLICK_WAIT)
        return True
    except Exception as e:
        logger.warning(f"点击有兴趣失败: {e}")
        # 尝试关闭菜单
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
            time.sleep(1)
        except Exception:
            pass
        return False


def click_not_interested(driver):
    """点击没兴趣按钮"""
    try:
        not_interested_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH,
                "//div[@role='button']//span[contains(text(),'没兴趣') or contains(text(),'Not interested')]"
                "/ancestor::div[@role='button']"
            ))
        )
        not_interested_btn.click()
        logger.info("已点击没兴趣")
        time.sleep(INTEREST_CLICK_WAIT)
        return True
    except Exception as e:
        logger.warning(f"点击没兴趣失败: {e}")
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
            time.sleep(1)
        except Exception:
            pass
        return False


def detect_like_restriction(driver):
    """检测点赞后是否遇到风控弹窗"""
    try:
        restriction_selectors = [
            "//span[contains(text(), '你暂时无法使用这个功能')]",
            "//span[contains(text(), '你暂时无法使用这项功能')]",
            "//span[contains(text(), 'temporarily unable to use this feature')]",
            "//span[contains(text(), '操作过于频繁')]",
            "//span[contains(text(), '请稍后再试')]",
            "//span[contains(text(), 'try again later')]",
            "//span[contains(text(), '暂时限制')]",
            "//span[contains(text(), 'temporarily restricted')]",
            "//span[contains(text(), '你的账号被暂时限制')]",
            "//span[contains(text(), 'your account has been temporarily')]",
            "//div[contains(text(), '你暂时无法使用这个功能')]",
            "//div[contains(text(), '你暂时无法使用这项功能')]",
        ]
        for selector in restriction_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed():
                        text = element.text.strip()
                        logger.warning(f"检测到点赞风控: {text}")
                        return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def click_like(post_element, driver):
    """点击赞按钮，返回 (是否成功, 是否被风控)"""
    try:
        like_btn = post_element.find_element(By.XPATH,
            ".//div[@aria-label='赞' or @aria-label='Like'][@role='button']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", like_btn)
        time.sleep(0.3)
        like_btn.click()
        logger.info("已点赞")
        time.sleep(2)

        # 点赞后检测风控
        if detect_like_restriction(driver):
            # 尝试关闭风控弹窗
            try:
                close_btns = driver.find_elements(By.XPATH,
                    "//div[@aria-label='关闭' or @aria-label='Close'][@role='button']"
                    " | //div[@role='button']//span[text()='确定' or text()='OK' or text()='好的']")
                for btn in close_btns:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
                        break
            except Exception:
                pass
            return True, True  # 点赞成功但被风控

        return True, False  # 点赞成功无风控
    except Exception as e:
        logger.warning(f"点赞失败: {e}")
        return False, False


def human_scroll(driver, pixels=None):
    """人类化滚动"""
    if pixels is None:
        pixels = random.randint(400, 800)

    # 分段滚动，模拟人类
    segments = random.randint(3, 6)
    per_segment = pixels // segments

    for _ in range(segments):
        offset = per_segment + random.randint(-30, 30)
        driver.execute_script(f"window.scrollBy(0, {offset});")
        time.sleep(random.uniform(0.1, 0.3))

    # 随机小幅回滚
    if random.random() < 0.2:
        driver.execute_script(f"window.scrollBy(0, {-random.randint(20, 60)});")
        time.sleep(random.uniform(0.3, 0.6))


def wait_for_posts_load(driver, timeout=None, min_posts=3):
    """等待帖子加载，直到至少出现 min_posts 个帖子"""
    timeout = timeout or POST_LOAD_TIMEOUT
    try:
        # 先等待至少1个帖子出现
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='article']"))
        )
        # 继续等待直到帖子数达到 min_posts 或超时
        end_time = time.time() + timeout
        while time.time() < end_time:
            count = len(driver.find_elements(By.XPATH, "//div[@role='article']"))
            if count >= min_posts:
                logger.info(f"帖子加载完成，已检测到 {count} 个帖子")
                return True
            time.sleep(1)
        # 超时但至少有1个帖子
        count = len(driver.find_elements(By.XPATH, "//div[@role='article']"))
        logger.info(f"等待更多帖子超时，当前 {count} 个")
        return count > 0
    except Exception:
        logger.warning("等待帖子加载超时，未检测到任何帖子")
        return False


def extract_post_data(post_element, driver, page_name, account_name=None):
    """提取阶段：从帖子元素中提取数据，不做AI分析（需要浏览器，必须顺序执行）
    返回 dict 或 None（如果帖子应被跳过）"""
    # 1. 提取帖子ID
    post_id = extract_post_id(post_element)
    if not post_id:
        logger.debug("未找到帖子ID，跳过")
        return None

    # 2. 检查去重
    if is_post_exists(post_id):
        logger.info(f"帖子 {post_id} 已存在，跳过")
        return None

    logger.info(f"提取新帖子数据: {post_id}")

    # 3. 滚动到帖子可见
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", post_element)
        time.sleep(0.5)
    except Exception:
        pass

    # 4. 获取帖子完整内容
    content = get_full_post_content(post_element, driver)

    # 5. 跳过无文本的帖子
    if not content or len(content.strip()) < 10:
        logger.info(f"帖子 {post_id} 无文本内容，跳过")
        return None

    # 6. 提取元数据
    post_url = extract_post_url(post_element)
    author_name, author_id, author_profile_url = extract_author_info(post_element)
    post_time = extract_post_time(post_element)

    # 6.1 预过滤
    if _is_junk_content(content):
        logger.info(f"帖子 {post_id} 内容无实质文字，跳过")
        return None
    if _is_non_business_content(content):
        logger.info(f"帖子 {post_id} 为交友/社交帖，跳过")
        return None

    # 6.2 清洗内容
    clean_content = _clean_content_for_ai(content, author_name)
    if not clean_content or len(clean_content.strip()) < 10:
        logger.info(f"帖子 {post_id} 清洗后无实质内容，跳过")
        return None

    logger.info(f"帖子内容: {clean_content[:100]}...")

    return {
        "post_id": post_id,
        "post_url": post_url,
        "author_name": author_name,
        "author_id": author_id,
        "author_profile_url": author_profile_url,
        "content": content,
        "clean_content": clean_content,
        "post_time": post_time,
        "source_page": page_name,
        "discovered_by": account_name,
        "post_element": post_element,  # 保留引用，用于后续交互
    }


def _analyze_single_post(extracted):
    """对单个已提取的帖子进行并发AI三选二分析（纯计算，不需要浏览器）"""
    post_id = extracted["post_id"]
    clean_content = extracted["clean_content"]

    logger.info(f"开始并发AI分析帖子 {post_id} (三选二投票)...")
    is_target, ai_response, votes = analyze_post_concurrent(clean_content)

    vote_summary = ", ".join(["是" if v else "否" for v in votes])
    final_label = "目标客户" if is_target else "非目标客户"
    logger.info(f"[投票结果] {post_id}: {vote_summary} → {final_label}")

    extracted["is_target"] = is_target
    extracted["ai_result"] = ai_response
    return extracted


def batch_analyze_posts(extracted_list):
    """批量并发分析多个帖子（最多AI_BATCH_SIZE个同时分析）"""
    if not extracted_list:
        return []

    logger.info(f"开始批量并发分析 {len(extracted_list)} 个帖子...")
    results = []

    with ThreadPoolExecutor(max_workers=min(len(extracted_list), AI_BATCH_SIZE)) as executor:
        future_to_post = {
            executor.submit(_analyze_single_post, ex): ex
            for ex in extracted_list
        }
        for future in as_completed(future_to_post):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                ex = future_to_post[future]
                logger.error(f"分析帖子 {ex['post_id']} 异常: {e}")
                ex["is_target"] = False
                ex["ai_result"] = f"分析异常: {e}"
                results.append(ex)

    logger.info(f"批量分析完成，共 {len(results)} 个帖子")
    return results


def interact_and_save_post(analyzed_data, driver, account_name=None):
    """交互并保存阶段：对已分析的帖子执行交互操作并保存到数据库（需要浏览器，必须顺序执行）"""
    post_id = analyzed_data["post_id"]
    is_target = analyzed_data["is_target"]
    post_element = analyzed_data.get("post_element")

    post_data = {
        "post_id": post_id,
        "post_url": analyzed_data.get("post_url"),
        "author_name": analyzed_data.get("author_name"),
        "author_id": analyzed_data.get("author_id"),
        "author_profile_url": analyzed_data.get("author_profile_url"),
        "content": analyzed_data.get("content"),
        "post_time": analyzed_data.get("post_time"),
        "source_page": analyzed_data.get("source_page"),
        "ai_result": analyzed_data.get("ai_result"),
        "is_target": is_target,
        "action_interested": False,
        "action_not_interested": False,
        "action_liked": False,
        "discovered_by": account_name,
    }

    # 交互操作 - 目标帖子必须点击有兴趣，非目标帖子低概率点击没兴趣
    if post_element:
        try:
            if is_target:
                # 目标帖子：必须点击有兴趣
                update_status(last_action=f"点击有兴趣 - {post_id}")
                if click_three_dots_menu(post_element, driver):
                    if click_interested(driver):
                        post_data["action_interested"] = True
                random_delay()
            else:
                # 非目标帖子：低概率点击没兴趣，且延迟更长避免风控
                if random.random() < NOT_INTERESTED_PROBABILITY:
                    update_status(last_action=f"点击没兴趣 - {post_id}")
                    # 点击没兴趣前增加较长延迟
                    random_delay(NOT_INTERESTED_DELAY_MIN, NOT_INTERESTED_DELAY_MAX)
                    if click_three_dots_menu(post_element, driver):
                        if click_not_interested(driver):
                            post_data["action_not_interested"] = True
                    random_delay()
        except Exception as e:
            logger.warning(f"帖子 {post_id} 交互操作异常: {e}")

        # 点赞：极低概率（被风控的账号跳过）
        try:
            if account_name and account_name in _like_disabled_accounts:
                pass  # 已被风控，跳过
            elif is_target and random.random() < LIKE_PROBABILITY:
                update_status(last_action=f"点赞 - {post_id}")
                liked, restricted = click_like(post_element, driver)
                if liked:
                    post_data["action_liked"] = True
                if restricted and account_name:
                    _like_disabled_accounts.add(account_name)
                    logger.warning(f"[{account_name}] 点赞遇到风控，已停止该账号的点赞功能")
                random_delay()
        except Exception as e:
            logger.warning(f"帖子 {post_id} 点赞操作异常: {e}")

    # 保存到数据库
    saved = save_post(post_data)
    if saved:
        logger.info(f"帖子 {post_id} 已保存到数据库")
        if is_target:
            try:
                from task_queue import generate_tasks_for_post
                generate_tasks_for_post(saved)
                logger.info(f"帖子 {post_id} 的发送任务已生成")
            except Exception as e:
                logger.warning(f"生成发送任务失败: {e}")
    else:
        logger.error(f"帖子 {post_id} 保存失败")

    return saved


def monitor_single_page(driver, page_config, tab_index, account_name=None):
    """监控单个页面 - 批量提取+并发AI分析+顺序交互"""
    page_name = page_config["name"]
    page_label = page_config["label"]

    update_status(
        current_page=page_name,
        current_page_label=page_label,
        posts_processed=0,
    )
    if account_name:
        update_account_status(account_name, current_page=page_name)

    # 切换到对应标签页
    if not switch_to_tab(driver, tab_index):
        return 0

    # 刷新页面
    refresh_page(driver, page_config, tab_index, account_name=account_name)

    # 记录日志（提前创建，确保任何退出路径都能更新）
    session = get_session()
    log = MonitorLog(
        page_type=page_name,
        account_name=account_name,
        started_at=datetime.now(timezone.utc),
    )
    session.add(log)
    session.commit()
    log_id = log.id
    session.close()

    posts_processed = 0
    posts_scanned = 0

    try:
        # 等待帖子加载
        if not wait_for_posts_load(driver):
            logger.warning(f"{page_label}: 未检测到帖子")
            return 0

        # === 预加载阶段：慢速滚动让页面加载更多帖子 ===
        logger.info(f"{page_label}: 开始预加载滚动...")
        prev_count = 0
        no_growth_count = 0
        for i in range(8):
            # 慢速滚动到当前页面底部附近
            driver.execute_script(
                "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});"
            )
            # 每次滚动后等待2-4秒，模拟人类阅读节奏，避免风控
            random_delay(2, 4)

            # 检查帖子数量是否增长
            cur_count = len(driver.find_elements(By.XPATH, "//div[@role='article']"))
            logger.info(f"{page_label}: 预加载第{i+1}轮, 帖子数: {cur_count}")
            if cur_count == prev_count:
                no_growth_count += 1
                if no_growth_count >= 3:
                    logger.info(f"{page_label}: 帖子数不再增长，停止预加载")
                    break
            else:
                no_growth_count = 0
            prev_count = cur_count

        # 滚回顶部，从头开始扫描
        driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
        random_delay(1.5, 2.5)

        # === 扫描阶段：一次性提取所有帖子 ===
        processed_ids_this_round = set()
        no_new_posts_count = 0

        while posts_processed < MAX_POSTS_PER_PAGE:
            try:
                post_elements = driver.find_elements(By.XPATH, "//div[@role='article']")
                current_count = len(post_elements)
                logger.info(f"{page_label}: 找到 {current_count} 个帖子元素")

                if current_count == 0:
                    logger.info("未找到帖子，尝试滚动...")
                    human_scroll(driver, random.randint(800, 1500))
                    random_delay(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX)
                    no_new_posts_count += 1
                    if no_new_posts_count > 8:
                        logger.info("连续8次未找到帖子，结束当前页面")
                        break
                    continue

                # === 阶段1: 提取当前可见的所有帖子（不再受 AI_BATCH_SIZE 限制） ===
                batch_extracted = []
                for post in post_elements:
                    if posts_processed + len(batch_extracted) >= MAX_POSTS_PER_PAGE:
                        break

                    try:
                        post_id = extract_post_id(post)
                        if post_id and post_id in processed_ids_this_round:
                            continue
                        if post_id:
                            processed_ids_this_round.add(post_id)

                        posts_scanned += 1
                        update_status(posts_processed=posts_processed, posts_total=posts_scanned)

                        extracted = extract_post_data(post, driver, page_name, account_name=account_name)
                        if extracted:
                            batch_extracted.append(extracted)

                        # 提取间的短暂延迟
                        random_delay(0.3, 0.8)

                    except Exception as e:
                        logger.error(f"提取帖子数据出错: {e}")
                        continue

                if not batch_extracted:
                    no_new_posts_count += 1
                    if no_new_posts_count > 5:
                        logger.info("连续多次无新帖子，结束当前页面")
                        break
                    # 滚动前检查并关闭遮罩层
                    dismiss_overlay(driver)
                    human_scroll(driver, random.randint(1000, 2000))
                    random_delay(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX)
                    continue

                no_new_posts_count = 0

                # === 阶段2: 分批并发AI分析（不需要浏览器） ===
                # 将提取的帖子按 AI_BATCH_SIZE 分批处理
                for batch_start in range(0, len(batch_extracted), AI_BATCH_SIZE):
                    batch = batch_extracted[batch_start:batch_start + AI_BATCH_SIZE]
                    logger.info(f"开始批量分析 {len(batch)} 个帖子 (第{batch_start//AI_BATCH_SIZE + 1}批)...")
                    update_status(last_action=f"批量AI分析 {len(batch)} 个帖子")
                    analyzed_list = batch_analyze_posts(batch)

                    # === 阶段3: 顺序执行交互操作并保存（需要浏览器） ===
                    for analyzed in analyzed_list:
                        try:
                            result = interact_and_save_post(analyzed, driver, account_name=account_name)
                            if result:
                                posts_processed += 1
                                update_status(posts_processed=posts_processed)
                            random_delay(ACTION_WAIT_MIN, ACTION_WAIT_MAX)
                        except Exception as e:
                            logger.error(f"交互保存帖子 {analyzed.get('post_id')} 出错: {e}")

                # 滚动前检查并关闭遮罩层
                dismiss_overlay(driver)

                # 滚动加载更多
                logger.info("向下滚动加载更多帖子...")
                human_scroll(driver, random.randint(1000, 2000))
                random_delay(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX)

            except Exception as e:
                logger.error(f"监控页面循环出错: {e}")
                break

    finally:
        # 无论正常退出还是异常退出，都更新日志记录
        session = get_session()
        try:
            log = session.query(MonitorLog).filter(MonitorLog.id == log_id).first()
            if log:
                log.posts_scanned = posts_scanned
                log.posts_new = posts_processed
                log.finished_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as e:
            logger.error(f"更新监控日志失败: {e}")
            session.rollback()
        finally:
            session.close()

    logger.info(f"{page_label}: 扫描 {posts_scanned} 个帖子，处理 {posts_processed} 个新帖子")
    return posts_processed


def _monitor_page_loop(driver, page_config, tab_index, account_name, page_label_suffix=""):
    """单个页面的持续监控循环（独立浏览器实例，独立线程运行）"""
    page_name = page_config["name"]
    page_label = page_config["label"]
    thread_label = f"[{account_name}-{page_name}]"

    round_count = 0
    while monitor_status["running"]:
        round_count += 1
        logger.info(f"\n{thread_label} ===== 监控第 {round_count} 轮 =====")

        try:
            # 检查并关闭遮罩层
            dismiss_overlay(driver)

            logger.info(f"{thread_label} 开始监控: {page_label}")
            new_posts = monitor_single_page(driver, page_config, tab_index, account_name=account_name)
            logger.info(f"{thread_label} 第 {round_count} 轮完成，处理 {new_posts} 个新帖子")

        except Exception as e:
            logger.error(f"{thread_label} 监控轮次出错: {e}")

        # 每轮之后等待几秒再继续
        wait_time = random.uniform(ROUND_INTERVAL_MIN, ROUND_INTERVAL_MAX)
        logger.info(f"{thread_label} 等待 {wait_time:.1f}s 后开始下一轮...")
        time.sleep(wait_time)

    logger.info(f"{thread_label} 监控循环已停止")


def start_monitor_for_account(account_name, cookie_url):
    """为指定账号启动监控循环 - 首页和小组并行监控（各自独立浏览器）"""
    update_account_status(account_name, running=True, error="")

    # 1. 下载cookies
    logger.info(f"[{account_name}] 步骤1: 下载Cookie文件...")
    cookies_file = download_cookies(cookie_url=cookie_url, account_name=account_name)
    if not cookies_file:
        update_account_status(account_name, running=False, error="Cookie下载失败")
        return

    # 2. 为每个监控页面创建独立浏览器实例
    drivers = []
    page_threads = []

    try:
        for i, page_config in enumerate(MONITOR_PAGES):
            page_name = page_config["name"]
            logger.info(f"[{account_name}] 创建 {page_config['label']} 专用浏览器...")

            driver = create_driver()
            if not driver:
                logger.error(f"[{account_name}] {page_config['label']} 浏览器创建失败")
                update_account_status(account_name, running=False, error=f"{page_config['label']}浏览器创建失败")
                # 清理已创建的浏览器
                for d in drivers:
                    try:
                        d.quit()
                    except Exception:
                        pass
                return

            # 加载cookies
            logger.info(f"[{account_name}] 加载 {page_config['label']} Cookie...")
            if not load_cookies(driver, cookies_file):
                logger.error(f"[{account_name}] {page_config['label']} Cookie加载失败")
                update_account_status(account_name, running=False, error=f"{page_config['label']} Cookie加载失败")
                driver.quit()
                for d in drivers:
                    try:
                        d.quit()
                    except Exception:
                        pass
                return

            # 导航到对应页面
            logger.info(f"[{account_name}] 打开 {page_config['label']} 页面...")
            driver.get(page_config["url"])
            time.sleep(3)

            drivers.append(driver)

            # 错开浏览器启动
            if i < len(MONITOR_PAGES) - 1:
                time.sleep(3)

        # 3. 为每个页面启动独立监控线程
        logger.info(f"[{account_name}] 所有浏览器就绪，启动并行监控线程...")

        for i, page_config in enumerate(MONITOR_PAGES):
            t = threading.Thread(
                target=_monitor_page_loop,
                args=(drivers[i], page_config, 0, account_name),
                daemon=True,
            )
            page_threads.append(t)
            t.start()
            logger.info(f"[{account_name}] {page_config['label']} 监控线程已启动")

        update_account_status(account_name, round_count=0)

        # 4. 主线程等待所有页面监控线程
        while monitor_status["running"]:
            # 检查线程存活状态
            alive = [t.is_alive() for t in page_threads]
            if not any(alive):
                logger.warning(f"[{account_name}] 所有监控线程已停止")
                break
            time.sleep(5)

    except KeyboardInterrupt:
        logger.info(f"[{account_name}] 监控被用户中断")
    except Exception as e:
        logger.error(f"[{account_name}] 监控出错: {e}")
        update_account_status(account_name, error=str(e))
    finally:
        update_account_status(account_name, running=False)
        logger.info(f"[{account_name}] 关闭所有浏览器...")
        for d in drivers:
            try:
                d.quit()
            except Exception:
                pass


def start_monitor():
    """启动监控主循环（兼容旧的单账号模式，同时支持多账号）"""
    update_status(running=True, error="")

    # 查询数据库中已启用的monitor账号
    session = get_session()
    try:
        monitor_accounts = session.query(Account).filter(
            Account.account_type == 'monitor',
            Account.enabled == True
        ).all()
        accounts_list = [(a.name, a.cookie_url) for a in monitor_accounts if a.cookie_url]
    finally:
        session.close()

    if accounts_list:
        # 多账号模式：为每个账号启动独立监控线程
        logger.info(f"发现 {len(accounts_list)} 个监控账号，启动多账号监控...")
        for name, cookie_url in accounts_list:
            t = threading.Thread(
                target=start_monitor_for_account,
                args=(name, cookie_url),
                daemon=True
            )
            _monitor_threads[name] = t
            t.start()
            time.sleep(5)  # 错开启动时间

        # 主循环等待所有线程
        try:
            while monitor_status["running"]:
                time.sleep(5)
        except KeyboardInterrupt:
            monitor_status["running"] = False
    else:
        # 兼容模式：使用config中的默认COOKIE_URL
        logger.info("未找到数据库中的监控账号，使用默认Cookie配置...")
        start_monitor_for_account("default", COOKIE_URL)


def start_monitor_thread():
    """在后台线程中启动监控"""
    if monitor_status["running"]:
        logger.warning("监控已在运行中")
        return False

    thread = threading.Thread(target=start_monitor, daemon=True)
    thread.start()
    return True


def stop_monitor():
    """停止监控"""
    monitor_status["running"] = False
    logger.info("已发送停止信号")
