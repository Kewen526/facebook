"""
发帖引擎 - 负责自动在Facebook发布推广帖子
每个发送账号每天自动发布一篇AI生成的推广帖子
"""
import time
import random
import logging
import threading
from datetime import datetime, timezone, date

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains

from monitor import create_driver, load_cookies, download_cookies, dismiss_overlay
from models import get_session, Account, PostPublish
from ai_analyzer import generate_post_content

logger = logging.getLogger(__name__)

# 全局状态
_publisher_running = False
_publisher_thread = None
_publish_status = {}  # account_id -> {状态信息}


def publish_post_with_driver(driver, content):
    """使用已初始化的浏览器发布帖子

    Args:
        driver: Selenium WebDriver实例
        content: 要发布的帖子内容

    Returns:
        (success, detail) tuple
    """
    try:
        # 导航到Facebook首页
        driver.get("https://www.facebook.com/")
        time.sleep(random.uniform(3, 5))

        # 关闭遮罩层
        dismiss_overlay(driver)
        time.sleep(1)

        # 点击发帖入口（"你在想什么？" / "What's on your mind?"）
        # 使用通用属性定位，兼容多语言
        create_post_selectors = [
            # role=button 的发帖入口
            "//div[@role='button' and @tabindex='0']//span[contains(@style, 'webkit') or contains(@class, 'x1lliihq')]",
            # 直接匹配"你在想什么"类文本的容器
            "//div[@role='button' and contains(@class, 'x1i10hfl')]//span",
            # contenteditable 的发帖区域
            "//div[@contenteditable='true' and @role='textbox' and @aria-label]",
            # 通用匹配: 首页顶部发帖区域
            "//div[contains(@class, 'x1lliihq')]//div[@role='button']",
        ]

        post_entry = None
        for selector in create_post_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    if elem.is_displayed() and elem.location['y'] < 600:
                        post_entry = elem
                        break
                if post_entry:
                    break
            except Exception:
                continue

        if not post_entry:
            # 备选方案：尝试直接查找页面顶部的发帖按钮区域
            try:
                post_entry = driver.find_element(By.XPATH,
                    "//div[@role='main']//div[@role='button'][1]")
            except Exception:
                pass

        if not post_entry:
            return False, "未找到发帖入口"

        # 点击发帖入口
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", post_entry)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", post_entry)
        time.sleep(random.uniform(3, 5))

        # 等待发帖对话框出现，查找文本编辑框
        post_box = None
        textbox_selectors = [
            "//div[@role='dialog']//div[@contenteditable='true' and @role='textbox']",
            "//div[@role='dialog']//div[@data-lexical-editor='true']",
            "//form//div[@contenteditable='true' and @role='textbox']",
            "//div[@contenteditable='true' and @role='textbox']",
        ]

        for selector in textbox_selectors:
            try:
                elements = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.XPATH, selector))
                )
                for elem in elements:
                    if elem.is_displayed():
                        post_box = elem
                        break
                if post_box:
                    break
            except Exception:
                continue

        if not post_box:
            return False, "未找到发帖文本框"

        # 点击并聚焦文本框
        driver.execute_script("arguments[0].click();", post_box)
        time.sleep(1)
        actions = ActionChains(driver)
        actions.move_to_element(post_box).click().perform()
        time.sleep(1)

        # 逐字符输入内容（模拟真人输入）
        try:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if i > 0:
                    # 换行
                    actions = ActionChains(driver)
                    actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
                    time.sleep(0.3)
                for char in line:
                    actions = ActionChains(driver)
                    actions.send_keys(char).perform()
                    time.sleep(random.uniform(0.03, 0.1))
        except Exception as input_err:
            logger.warning(f"send_keys输入失败: {input_err}, 使用JS注入")
            try:
                html_content = content.replace('\n', '<br>')
                driver.execute_script("""
                    var el = arguments[0];
                    el.focus();
                    el.innerHTML = arguments[1];
                    var evt = new InputEvent('input', {bubbles: true, cancelable: true});
                    el.dispatchEvent(evt);
                """, post_box, html_content)
                time.sleep(2)
            except Exception as js_err:
                return False, f"输入内容失败: {js_err}"

        time.sleep(random.uniform(2, 4))

        # 点击发布按钮（多语言兼容）
        submit_selectors = [
            # aria-label匹配发布按钮（多语言）
            "//div[@role='dialog']//div[@aria-label='发帖' or @aria-label='Post' or @aria-label='Publish'"
            " or @aria-label='Publier' or @aria-label='Publicar'"
            " or @aria-label='Yayınla' or @aria-label='Đăng'"
            " or @aria-label='Kirim' or @aria-label='نشر'][@role='button']",
            # span文本匹配
            "//div[@role='dialog']//div[@role='button']//span[text()='发帖' or text()='Post'"
            " or text()='Publish' or text()='Publier' or text()='Publicar']",
            # 兜底：对话框内的提交按钮
            "//div[@role='dialog']//div[@role='button'][contains(@class, 'x1ja2u2z')]",
        ]

        submit_btn = None
        for selector in submit_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    if elem.is_displayed():
                        submit_btn = elem
                        break
                if submit_btn:
                    break
            except Exception:
                continue

        if not submit_btn:
            return False, "未找到发布按钮"

        # 点击发布
        driver.execute_script("arguments[0].click();", submit_btn)
        time.sleep(random.uniform(5, 8))

        # 验证发布结果：检查对话框是否关闭
        try:
            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
            dialog_visible = any(d.is_displayed() for d in dialogs)
            if dialog_visible:
                # 对话框仍在，可能发布失败，再次尝试点击发布
                for selector in submit_selectors:
                    try:
                        elements = driver.find_elements(By.XPATH, selector)
                        for elem in elements:
                            if elem.is_displayed():
                                driver.execute_script("arguments[0].click();", elem)
                                time.sleep(5)
                                break
                    except Exception:
                        continue
        except Exception:
            pass

        logger.info("帖子发布成功")
        return True, "发布成功"

    except Exception as e:
        logger.error(f"发帖过程出错: {e}")
        return False, f"发帖过程出错: {e}"


def publish_for_account(account_id, account_name, cookie_url):
    """为单个账号执行发帖任务"""
    today = date.today()
    session = get_session()

    try:
        # 检查今天是否已发帖
        existing = session.query(PostPublish).filter(
            PostPublish.account_id == account_id,
            PostPublish.publish_date == today,
            PostPublish.status.in_(['success', 'publishing'])
        ).first()
        if existing:
            logger.info(f"[{account_name}] 今日已发帖，跳过")
            return

        # 生成帖子内容
        content = generate_post_content()
        if not content:
            logger.error(f"[{account_name}] AI生成发帖内容失败")
            record = PostPublish(
                account_id=account_id,
                content="",
                status='failed',
                publish_date=today,
                error_message='AI生成内容失败'
            )
            session.add(record)
            session.commit()
            return

        # 创建发帖记录
        record = PostPublish(
            account_id=account_id,
            content=content,
            status='publishing',
            publish_date=today,
        )
        session.add(record)
        session.commit()
        record_id = record.id

        _publish_status[account_id] = {
            "account_name": account_name,
            "status": "publishing",
            "content_preview": content[:100],
            "last_update": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        session.rollback()
        logger.error(f"[{account_name}] 创建发帖记录失败: {e}")
        return
    finally:
        session.close()

    # 初始化浏览器
    cookies_file = download_cookies(cookie_url=cookie_url, account_name=account_name)
    if not cookies_file:
        _update_publish_record(record_id, 'failed', 'Cookie下载失败')
        return

    driver = create_driver()
    if not driver:
        _update_publish_record(record_id, 'failed', '浏览器创建失败')
        return

    try:
        if not load_cookies(driver, cookies_file):
            _update_publish_record(record_id, 'failed', 'Cookie加载失败')
            return

        # 执行发帖
        success, detail = publish_post_with_driver(driver, content)

        if success:
            _update_publish_record(record_id, 'success', None)
            _publish_status[account_id] = {
                "account_name": account_name,
                "status": "success",
                "content_preview": content[:100],
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(f"[{account_name}] 发帖成功")
        else:
            _update_publish_record(record_id, 'failed', detail)
            _publish_status[account_id] = {
                "account_name": account_name,
                "status": "failed",
                "error": detail,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            logger.error(f"[{account_name}] 发帖失败: {detail}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _update_publish_record(record_id, status, error_message):
    """更新发帖记录状态"""
    session = get_session()
    try:
        record = session.query(PostPublish).filter(PostPublish.id == record_id).first()
        if record:
            record.status = status
            record.error_message = error_message
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"更新发帖记录失败: {e}")
    finally:
        session.close()


def run_publisher(user_id=None):
    """发帖任务主循环 - 检查所有发送账号并逐个发帖"""
    global _publisher_running
    _publisher_running = True

    logger.info("发帖任务处理器已启动")

    session = get_session()
    try:
        query = session.query(Account).filter(
            Account.account_type == 'sender',
            Account.enabled == True,
            Account.status.notin_(['banned', 'restricted'])
        )
        if user_id is not None:
            query = query.filter(Account.user_id == user_id)
        sender_accounts = query.all()
        accounts_info = [(a.id, a.name, a.cookie_url) for a in sender_accounts if a.cookie_url]
    finally:
        session.close()

    if not accounts_info:
        logger.warning("没有可用的发送账号")
        _publisher_running = False
        return

    logger.info(f"发现 {len(accounts_info)} 个发送账号，开始逐个发帖...")

    for account_id, account_name, cookie_url in accounts_info:
        if not _publisher_running:
            break

        _publish_status[account_id] = {
            "account_name": account_name,
            "status": "preparing",
            "last_update": datetime.now(timezone.utc).isoformat(),
        }

        try:
            publish_for_account(account_id, account_name, cookie_url)
        except Exception as e:
            logger.error(f"[{account_name}] 发帖异常: {e}")
            _publish_status[account_id] = {
                "account_name": account_name,
                "status": "error",
                "error": str(e),
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

        # 账号间间隔，避免同时操作
        if _publisher_running:
            time.sleep(random.uniform(10, 20))

    _publisher_running = False
    logger.info("发帖任务处理器已完成")


def start_publisher(user_id=None):
    """启动发帖任务处理器"""
    global _publisher_thread, _publisher_running

    if _publisher_running and _publisher_thread and _publisher_thread.is_alive():
        logger.warning("发帖处理器已在运行中")
        return False

    _publisher_thread = threading.Thread(target=run_publisher, args=(user_id,), daemon=True)
    _publisher_thread.start()
    logger.info("发帖处理器线程已启动")
    return True


def stop_publisher():
    """停止发帖任务处理器"""
    global _publisher_running
    _publisher_running = False
    logger.info("已发送停止信号给发帖处理器")


def get_publisher_status():
    """获取发帖状态"""
    return {
        "running": _publisher_running,
        "accounts": _publish_status,
    }
