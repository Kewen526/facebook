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

from config import (
    COOKIE_URL, COOKIES_DIR, USER_AGENTS, USER_DATA_DIR,
    MONITOR_PAGES, MAX_POSTS_PER_PAGE,
    INTEREST_PROBABILITY, LIKE_PROBABILITY,
    SCROLL_WAIT_MIN, SCROLL_WAIT_MAX,
    ACTION_WAIT_MIN, ACTION_WAIT_MAX,
    POST_LOAD_TIMEOUT, INTEREST_CLICK_WAIT,
    random_delay,
)
from models import is_post_exists, save_post, update_post_action, MonitorLog, get_session, Account
from ai_analyzer import analyze_post

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
        logger.info(f"已切换到标签页 {tab_index + 1}: {MONITOR_PAGES[tab_index]['label']}")
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
                time.sleep(2)
                return True

        elif refresh_type == "groups_link":
            # 小组页面 - 点击小组链接
            try:
                groups_link = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH,
                        "//a[contains(@href,'filter=groups') and contains(@href,'sk=h_chr')]"
                    ))
                )
                groups_link.click()
                logger.info("已点击小组链接")
                time.sleep(2)
                return True
            except Exception:
                logger.warning("未找到小组链接，使用导航刷新")
                driver.get(page_config["url"])
                time.sleep(2)
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


def wait_for_posts_load(driver, timeout=None):
    """等待帖子加载"""
    timeout = timeout or POST_LOAD_TIMEOUT
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='article']"))
        )
        time.sleep(2)
        return True
    except Exception:
        logger.warning("等待帖子加载超时")
        return False


def process_single_post(post_element, driver, page_name, account_name=None):
    """处理单个帖子 - 同步流程"""
    # 1. 提取帖子ID
    post_id = extract_post_id(post_element)
    if not post_id:
        logger.debug("未找到帖子ID，跳过")
        return None

    # 2. 检查去重
    if is_post_exists(post_id):
        logger.info(f"帖子 {post_id} 已存在，跳过")
        return None

    logger.info(f"处理新帖子: {post_id}")
    update_status(last_action=f"处理帖子 {post_id}")

    # 3. 滚动到帖子可见
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", post_element)
        time.sleep(0.5)
    except Exception:
        pass

    # 4. 获取帖子完整内容
    content = get_full_post_content(post_element, driver)

    # 5. 跳过无文本的帖子（纯图片/视频帖无法用文本AI分析）
    if not content or len(content.strip()) < 10:
        logger.info(f"帖子 {post_id} 无文本内容，跳过")
        return None

    # 6. 提取元数据
    post_url = extract_post_url(post_element)
    author_name, author_id, author_profile_url = extract_author_info(post_element)
    post_time = extract_post_time(post_element)

    # 6.1 预过滤：纯标点/表情/链接等垃圾内容直接跳过
    if _is_junk_content(content):
        logger.info(f"帖子 {post_id} 内容无实质文字，跳过")
        return None

    # 6.2 预过滤：交友/征婚等明显非商业帖子直接跳过
    if _is_non_business_content(content):
        logger.info(f"帖子 {post_id} 为交友/社交帖，跳过")
        return None

    # 6.3 清洗内容：移除小组名称、作者名等非正文信息
    clean_content = _clean_content_for_ai(content, author_name)
    if not clean_content or len(clean_content.strip()) < 10:
        logger.info(f"帖子 {post_id} 清洗后无实质内容，跳过")
        return None

    update_status(last_post_content=content[:200])
    logger.info(f"帖子内容: {clean_content[:100]}...")

    # 7. 同步AI分析（使用清洗后的内容）- 三选二投票逻辑
    update_status(last_action=f"AI分析帖子 {post_id}")
    logger.info(f"开始AI分析帖子 {post_id} (三选二投票)...")

    votes = []
    ai_responses = []
    for vote_round in range(3):
        round_is_target, round_response = analyze_post(clean_content)
        votes.append(round_is_target)
        ai_responses.append(round_response)
        vote_label = "是" if round_is_target else "否"
        logger.info(f"[投票] 第{vote_round + 1}轮: {vote_label}")

    # 三选二：至少2票"是"才判定为目标客户
    yes_count = sum(1 for v in votes if v)
    is_target = yes_count >= 2
    vote_summary = ", ".join(["是" if v else "否" for v in votes])
    final_label = "目标客户" if is_target else "非目标客户"
    logger.info(f"[投票结果] {vote_summary} → 最终: {final_label} ({yes_count}/3)")

    # 合并AI响应，记录投票详情
    ai_response = f"=== 投票结果: {vote_summary} → {final_label} ({yes_count}/3) ===\n\n"
    for i, resp in enumerate(ai_responses):
        ai_response += f"--- 第{i+1}轮 ({('是' if votes[i] else '否')}) ---\n{resp}\n\n"

    # 8. 保存到数据库
    post_data = {
        "post_id": post_id,
        "post_url": post_url,
        "author_name": author_name,
        "author_id": author_id,
        "author_profile_url": author_profile_url,
        "content": content,
        "post_time": post_time,
        "source_page": page_name,
        "ai_result": ai_response,
        "is_target": is_target,
        "action_interested": False,
        "action_not_interested": False,
        "action_liked": False,
        "discovered_by": account_name,
    }

    # 9. 交互操作 - 80%概率点击有兴趣/没兴趣
    if random.random() < INTEREST_PROBABILITY:
        update_status(last_action=f"点击{'有兴趣' if is_target else '没兴趣'} - {post_id}")
        if click_three_dots_menu(post_element, driver):
            if is_target:
                if click_interested(driver):
                    post_data["action_interested"] = True
            else:
                if click_not_interested(driver):
                    post_data["action_not_interested"] = True
        random_delay()

    # 10. 0.5%概率点赞（被风控的账号跳过）
    if account_name and account_name in _like_disabled_accounts:
        logger.info(f"[{account_name}] 该账号已被风控，跳过点赞")
    elif random.random() < LIKE_PROBABILITY:
        update_status(last_action=f"点赞 - {post_id}")
        liked, restricted = click_like(post_element, driver)
        if liked:
            post_data["action_liked"] = True
        if restricted and account_name:
            _like_disabled_accounts.add(account_name)
            logger.warning(f"[{account_name}] 点赞遇到风控，已停止该账号的点赞功能")
        random_delay()

    # 11. 保存到数据库
    saved = save_post(post_data)
    if saved:
        logger.info(f"帖子 {post_id} 已保存到数据库")
        # 如果是目标客户，自动生成发送任务
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
    """监控单个页面 - 最多处理MAX_POSTS_PER_PAGE个帖子"""
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

    # 等待帖子加载
    if not wait_for_posts_load(driver):
        logger.warning(f"{page_label}: 未检测到帖子")
        return 0

    # 记录日志
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
    processed_ids_this_round = set()
    no_new_posts_count = 0

    while posts_processed < MAX_POSTS_PER_PAGE:
        try:
            post_elements = driver.find_elements(By.XPATH, "//div[@role='article']")
            current_count = len(post_elements)
            logger.info(f"{page_label}: 找到 {current_count} 个帖子元素")

            if current_count == 0:
                logger.info("未找到帖子，尝试滚动...")
                human_scroll(driver)
                random_delay(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX)
                no_new_posts_count += 1
                if no_new_posts_count > 5:
                    logger.info("连续5次未找到新帖子，结束当前页面")
                    break
                continue

            found_new = False
            for post in post_elements:
                if posts_processed >= MAX_POSTS_PER_PAGE:
                    break

                try:
                    # 快速提取ID检查去重
                    post_id = extract_post_id(post)
                    if post_id and post_id in processed_ids_this_round:
                        continue

                    if post_id:
                        processed_ids_this_round.add(post_id)

                    posts_scanned += 1
                    update_status(posts_processed=posts_processed, posts_total=posts_scanned)

                    result = process_single_post(post, driver, page_name, account_name=account_name)
                    if result:
                        posts_processed += 1
                        found_new = True
                        update_status(posts_processed=posts_processed)

                    # 随机延迟
                    random_delay(ACTION_WAIT_MIN, ACTION_WAIT_MAX)

                except Exception as e:
                    logger.error(f"处理帖子出错: {e}")
                    continue

            if not found_new:
                no_new_posts_count += 1
            else:
                no_new_posts_count = 0

            if no_new_posts_count > 3:
                logger.info("连续多次无新帖子，结束当前页面")
                break

            # 滚动前检查并关闭遮罩层
            dismiss_overlay(driver)

            # 滚动加载更多
            logger.info("向下滚动加载更多帖子...")
            human_scroll(driver, random.randint(600, 1200))
            random_delay(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX)

        except Exception as e:
            logger.error(f"监控页面循环出错: {e}")
            break

    # 更新日志
    session = get_session()
    try:
        log = session.query(MonitorLog).filter(MonitorLog.id == log_id).first()
        if log:
            log.posts_scanned = posts_scanned
            log.posts_new = posts_processed
            log.finished_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        session.close()

    logger.info(f"{page_label}: 扫描 {posts_scanned} 个帖子，处理 {posts_processed} 个新帖子")
    return posts_processed


def start_monitor_for_account(account_name, cookie_url):
    """为指定账号启动监控循环"""
    update_account_status(account_name, running=True, error="")

    # 1. 下载cookies
    logger.info(f"[{account_name}] 步骤1: 下载Cookie文件...")
    cookies_file = download_cookies(cookie_url=cookie_url, account_name=account_name)
    if not cookies_file:
        update_account_status(account_name, running=False, error="Cookie下载失败")
        return

    # 2. 创建浏览器
    logger.info(f"[{account_name}] 步骤2: 创建浏览器实例...")
    driver = create_driver()
    if not driver:
        update_account_status(account_name, running=False, error="浏览器创建失败")
        return

    try:
        # 3. 加载cookies
        logger.info(f"[{account_name}] 步骤3: 加载Cookie...")
        if not load_cookies(driver, cookies_file):
            update_account_status(account_name, running=False, error="Cookie加载失败")
            return

        # 4. 打开监控标签页（首页+小组）
        logger.info(f"[{account_name}] 步骤4: 打开监控标签页...")
        open_all_tabs(driver, account_name=account_name)

        # 5. 开始循环监控
        round_count = 0
        while monitor_status["running"]:
            round_count += 1
            update_account_status(account_name, round_count=round_count)
            logger.info(f"\n[{account_name}] ===== 监控第 {round_count} 轮 =====")

            total_new = 0
            for i, page_config in enumerate(MONITOR_PAGES):
                if not monitor_status["running"]:
                    break

                # 检查并关闭遮罩层
                dismiss_overlay(driver)

                logger.info(f"[{account_name}] --- 开始监控: {page_config['label']} ---")
                new_posts = monitor_single_page(driver, page_config, i, account_name=account_name)
                total_new += new_posts

                if i < len(MONITOR_PAGES) - 1:
                    delay = random.uniform(2, 4)
                    time.sleep(delay)

            update_account_status(account_name, posts_processed=total_new)
            logger.info(f"[{account_name}] 第 {round_count} 轮完成，共处理 {total_new} 个新帖子")

            wait_time = random.uniform(5, 10)
            time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info(f"[{account_name}] 监控被用户中断")
    except Exception as e:
        logger.error(f"[{account_name}] 监控出错: {e}")
        update_account_status(account_name, error=str(e))
    finally:
        update_account_status(account_name, running=False)
        logger.info(f"[{account_name}] 关闭浏览器...")
        try:
            driver.quit()
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
