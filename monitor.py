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
from models import is_post_exists, save_post, update_post_action, MonitorLog, get_session
from ai_analyzer import analyze_post

logger = logging.getLogger(__name__)

# 全局状态 - 供Flask API查询
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
}


def update_status(**kwargs):
    """更新监控状态"""
    monitor_status.update(kwargs)


def download_cookies():
    """从URL下载cookie文件"""
    logger.info(f"下载cookie文件: {COOKIE_URL}")
    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)
        cookie_file_path = os.path.join(COOKIES_DIR, "monitor_cookies.json")

        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'application/json, text/plain, */*',
        }
        proxies = {'http': None, 'https': None}

        response = requests.get(COOKIE_URL, headers=headers, proxies=proxies, timeout=30)
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
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
        driver = uc.Chrome(options=options, version_main=None)
        logger.info("使用undetected-chromedriver创建成功")
        return driver
    except Exception as e:
        logger.info(f"undetected-chromedriver不可用({e})，使用Selenium+反检测")

    # Selenium + 反检测
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

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
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        chrome_options.add_argument(f"--user-data-dir={USER_DATA_DIR}")

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


def open_all_tabs(driver):
    """打开三个监控页面的标签页"""
    logger.info("打开三个监控标签页...")

    # 第一个标签页 - 首页 (当前标签)
    driver.get(MONITOR_PAGES[0]["url"])
    time.sleep(5)
    logger.info(f"标签页1: {MONITOR_PAGES[0]['label']} 已打开")

    # 第二个标签页 - 小组
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[1])
    driver.get(MONITOR_PAGES[1]["url"])
    time.sleep(5)
    logger.info(f"标签页2: {MONITOR_PAGES[1]['label']} 已打开")

    # 第三个标签页 - 搜索
    driver.execute_script("window.open('');")
    driver.switch_to.window(driver.window_handles[2])
    driver.get(MONITOR_PAGES[2]["url"])
    time.sleep(5)
    logger.info(f"标签页3: {MONITOR_PAGES[2]['label']} 已打开")

    # 切回第一个标签页
    driver.switch_to.window(driver.window_handles[0])
    logger.info("所有标签页已打开，切回首页")


def switch_to_tab(driver, tab_index):
    """切换到指定标签页"""
    handles = driver.window_handles
    if tab_index < len(handles):
        driver.switch_to.window(handles[tab_index])
        time.sleep(2)
        logger.info(f"已切换到标签页 {tab_index + 1}: {MONITOR_PAGES[tab_index]['label']}")
        return True
    logger.error(f"标签页 {tab_index} 不存在")
    return False


def refresh_page(driver, page_config, tab_index):
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
                time.sleep(2)
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
                time.sleep(4)
                return True
            except Exception:
                # 备用方案: 直接导航
                logger.warning("未找到首页按钮，使用导航刷新")
                driver.get(page_config["url"])
                time.sleep(4)
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
                time.sleep(4)
                return True
            except Exception:
                logger.warning("未找到小组链接，使用导航刷新")
                driver.get(page_config["url"])
                time.sleep(4)
                return True

        elif refresh_type == "search_refilter":
            # 搜索页面 - 重新导航刷新
            driver.get(page_config["url"])
            logger.info("搜索页面已重新加载")
            time.sleep(5)
            return True

    except Exception as e:
        logger.error(f"刷新页面失败: {e}")
        driver.get(page_config["url"])
        time.sleep(5)
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
                    # 从URL中提取用户ID
                    match = re.search(r'facebook\.com/(?:profile\.php\?id=)?(\d+|[a-zA-Z0-9.]+)', href)
                    if match:
                        author_id = match.group(1)
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
                        match = re.search(r'facebook\.com/(?:profile\.php\?id=)?(\d+|[a-zA-Z0-9.]+)', href)
                        if match:
                            author_id = match.group(1)
                        break
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
    """获取帖子完整内容 - 包括展开操作"""
    try:
        original_content = post_element.text
        expand_indicators = ["展开", "See more", "Show more", "查看更多", "... 更多"]

        has_expand = any(indicator in original_content for indicator in expand_indicators)

        if has_expand:
            try:
                expand_btns = post_element.find_elements(By.XPATH,
                    ".//div[contains(@role,'button') and (contains(text(),'展开') or contains(text(),'See more') or contains(text(),'更多'))]"
                    " | .//span[contains(text(),'展开') or contains(text(),'See more')]/.."
                )
                for btn in expand_btns:
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                        time.sleep(0.5)
                        btn.click()
                        time.sleep(1.5)
                        break
                    except Exception:
                        continue
            except Exception:
                pass

        content = post_element.text
        return content if content else original_content

    except Exception as e:
        logger.error(f"获取帖子内容出错: {e}")
        return ""


def click_three_dots_menu(post_element, driver):
    """点击帖子的三个点菜单按钮"""
    try:
        # 查找三个点按钮 - 使用aria-label
        dots_btn = post_element.find_element(By.XPATH,
            ".//div[@aria-label='可对帖子执行的操作' or @aria-label='Actions for this post' or @aria-label='More']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", dots_btn)
        time.sleep(0.5)
        dots_btn.click()
        logger.info("已点击三个点菜单")
        time.sleep(2)
        return True
    except Exception:
        try:
            # 备用: 通过SVG路径查找
            dots_btn = post_element.find_element(By.XPATH,
                ".//svg[contains(@class,'x14rh7hd')]//path[contains(@d,'M458 360a2')]/../.."
                "/ancestor::div[@role='button']"
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", dots_btn)
            time.sleep(0.5)
            dots_btn.click()
            logger.info("已点击三个点菜单(备用方式)")
            time.sleep(2)
            return True
        except Exception as e:
            logger.warning(f"未找到三个点菜单: {e}")
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


def click_like(post_element, driver):
    """点击赞按钮"""
    try:
        like_btn = post_element.find_element(By.XPATH,
            ".//div[@aria-label='赞' or @aria-label='Like'][@role='button']"
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", like_btn)
        time.sleep(0.5)
        like_btn.click()
        logger.info("已点赞")
        time.sleep(2)
        return True
    except Exception as e:
        logger.warning(f"点赞失败: {e}")
        return False


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


def process_single_post(post_element, driver, page_name):
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
        time.sleep(1)
    except Exception:
        pass

    # 4. 获取帖子完整内容
    content = get_full_post_content(post_element, driver)
    if not content or len(content.strip()) < 10:
        logger.warning(f"帖子 {post_id} 内容过短，跳过")
        return None

    # 5. 提取元数据
    post_url = extract_post_url(post_element)
    author_name, author_id, author_profile_url = extract_author_info(post_element)
    post_time = extract_post_time(post_element)

    update_status(last_post_content=content[:200])
    logger.info(f"帖子内容: {content[:100]}...")

    # 6. 同步AI分析 - 必须等结果
    update_status(last_action=f"AI分析帖子 {post_id}")
    logger.info(f"开始AI分析帖子 {post_id}...")
    is_target, ai_response = analyze_post(content)
    logger.info(f"AI分析结果: {'目标客户' if is_target else '非目标客户'}")

    # 7. 保存到数据库
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
    }

    # 8. 交互操作 - 80%概率点击有兴趣/没兴趣
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

    # 9. 30%概率点赞
    if random.random() < LIKE_PROBABILITY:
        update_status(last_action=f"点赞 - {post_id}")
        if click_like(post_element, driver):
            post_data["action_liked"] = True
        random_delay()

    # 10. 保存到数据库
    saved = save_post(post_data)
    if saved:
        logger.info(f"帖子 {post_id} 已保存到数据库")
    else:
        logger.error(f"帖子 {post_id} 保存失败")

    return saved


def monitor_single_page(driver, page_config, tab_index):
    """监控单个页面 - 最多处理MAX_POSTS_PER_PAGE个帖子"""
    page_name = page_config["name"]
    page_label = page_config["label"]

    update_status(
        current_page=page_name,
        current_page_label=page_label,
        posts_processed=0,
    )

    # 切换到对应标签页
    if not switch_to_tab(driver, tab_index):
        return 0

    # 刷新页面
    refresh_page(driver, page_config, tab_index)

    # 等待帖子加载
    if not wait_for_posts_load(driver):
        logger.warning(f"{page_label}: 未检测到帖子")
        return 0

    # 记录日志
    session = get_session()
    log = MonitorLog(
        page_type=page_name,
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

                    result = process_single_post(post, driver, page_name)
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


def start_monitor():
    """启动监控主循环"""
    update_status(running=True, error="")

    # 1. 下载cookies
    logger.info("步骤1: 下载Cookie文件...")
    cookies_file = download_cookies()
    if not cookies_file:
        update_status(running=False, error="Cookie下载失败")
        return

    # 2. 创建浏览器
    logger.info("步骤2: 创建浏览器实例...")
    driver = create_driver()
    if not driver:
        update_status(running=False, error="浏览器创建失败")
        return

    try:
        # 3. 加载cookies
        logger.info("步骤3: 加载Cookie...")
        if not load_cookies(driver, cookies_file):
            update_status(running=False, error="Cookie加载失败")
            return

        # 4. 打开三个标签页
        logger.info("步骤4: 打开三个监控标签页...")
        open_all_tabs(driver)

        # 5. 开始循环监控
        round_count = 0
        while monitor_status["running"]:
            round_count += 1
            update_status(round_count=round_count)
            logger.info(f"\n{'='*60}")
            logger.info(f"===== 监控第 {round_count} 轮 =====")
            logger.info(f"{'='*60}")

            total_new = 0
            for i, page_config in enumerate(MONITOR_PAGES):
                if not monitor_status["running"]:
                    break

                logger.info(f"\n--- 开始监控: {page_config['label']} ---")
                new_posts = monitor_single_page(driver, page_config, i)
                total_new += new_posts

                # 页面间随机延迟
                if i < len(MONITOR_PAGES) - 1:
                    delay = random.uniform(3, 8)
                    logger.info(f"等待 {delay:.1f}s 后切换到下一个页面...")
                    time.sleep(delay)

            logger.info(f"\n第 {round_count} 轮完成，共处理 {total_new} 个新帖子")

            # 轮次间等待
            wait_time = random.uniform(10, 20)
            logger.info(f"等待 {wait_time:.1f}s 后开始下一轮...")
            time.sleep(wait_time)

    except KeyboardInterrupt:
        logger.info("监控被用户中断")
    except Exception as e:
        logger.error(f"监控出错: {e}")
        update_status(error=str(e))
    finally:
        update_status(running=False)
        logger.info("关闭浏览器...")
        try:
            driver.quit()
        except Exception:
            pass


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
