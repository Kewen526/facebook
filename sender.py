"""
发送引擎 - 负责执行评论、私信、加好友操作
从 V2.py 中提取并适配到主系统架构
"""
import time
import random
import logging

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains

from monitor import create_driver, load_cookies, download_cookies, dismiss_overlay
from config import COOKIES_DIR

logger = logging.getLogger(__name__)


def detect_sending_restriction(driver):
    """检测是否遇到发送限制（多语言支持）"""
    try:
        # 使用contains匹配，覆盖span和div，支持多语言
        restriction_keywords = [
            # 中文
            '你暂时无法使用这项功能', '你暂时无法使用这个功能',
            '你已达到陌生消息数量上限', '陌生消息已达到上限',
            '发送频率过快', '请稍后再试', '操作过于频繁', '暂时限制',
            '评论功能暂时不可用', '消息请求次数已达上限', '消息请求上限',
            '已达到消息请求限制',
            # 英文
            'temporarily unable to use this feature',
            'stranger message limit', 'try again later',
            'temporarily restricted', 'rate limited',
            'commenting is temporarily unavailable',
            'reached the message request limit',
            'limit to how many requests you can send',
            'message request limit',
            "You've reached the message request limit",
            # 法语
            'limite de demandes de message',
            'temporairement restreint',
            # 西班牙语
            'límite de solicitudes de mensaje',
            'temporalmente restringido',
            # 葡萄牙语
            'limite de solicitações de mensagem',
            'temporariamente restrito',
            # 阿拉伯语
            'حد طلبات الرسائل',
            # 土耳其语
            'mesaj istek sınırına ulaştınız',
            'geçici olarak kısıtlandı',
            # 越南语
            'giới hạn yêu cầu tin nhắn',
            # 印尼语/马来语
            'batas permintaan pesan',
        ]

        for kw in restriction_keywords:
            try:
                selector = f"//*[contains(text(), '{kw}')]"
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed():
                        restriction_text = element.text.strip()
                        logger.warning(f"检测到发送限制: {restriction_text}")
                        return True, restriction_text
            except Exception:
                continue

        return False, None
    except Exception as e:
        logger.error(f"检测发送限制时出错: {e}")
        return False, None


def detect_message_rate_limit(driver):
    """专门检测24小时消息请求限制（返回 True 表示已达上限需要停发24h）"""
    rate_limit_keywords = [
        # 英文
        'reached the message request limit',
        'limit to how many requests you can send',
        'message request limit',
        # 中文
        '已达到消息请求限制', '消息请求次数已达上限',
        '你已达到陌生消息数量上限', '陌生消息已达到上限',
        # 法语
        'limite de demandes de message',
        # 西班牙语
        'límite de solicitudes de mensaje',
        # 葡萄牙语
        'limite de solicitações de mensagem',
        # 土耳其语
        'mesaj istek sınırına ulaştınız',
    ]
    try:
        for kw in rate_limit_keywords:
            try:
                elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{kw}')]")
                for element in elements:
                    if element.is_displayed():
                        logger.warning(f"检测到24小时消息限制: {element.text.strip()}")
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False


class SenderEngine:
    """发送引擎 - 管理一个发送账号的浏览器和操作"""

    def __init__(self, account_name, cookie_url):
        self.account_name = account_name
        self.cookie_url = cookie_url
        self.driver = None
        self.initialized = False

    def initialize(self):
        """初始化浏览器并加载Cookie"""
        logger.info(f"[{self.account_name}] 初始化发送引擎...")

        # 下载Cookie
        cookies_file = download_cookies(cookie_url=self.cookie_url, account_name=self.account_name)
        if not cookies_file:
            logger.error(f"[{self.account_name}] Cookie下载失败")
            return False

        # 创建浏览器
        self.driver = create_driver()
        if not self.driver:
            logger.error(f"[{self.account_name}] 浏览器创建失败")
            return False

        # 加载Cookie
        if not load_cookies(self.driver, cookies_file):
            logger.error(f"[{self.account_name}] Cookie加载失败")
            self.cleanup()
            return False

        self.initialized = True
        logger.info(f"[{self.account_name}] 发送引擎初始化成功")
        return True

    def cleanup(self):
        """清理资源"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self.initialized = False

    def execute_comment(self, post_url, comment_text):
        """在帖子下发表评论"""
        if not self.initialized or not self.driver:
            return False, "发送引擎未初始化"

        logger.info(f"[{self.account_name}] 评论帖子: {post_url}")
        try:
            self.driver.get(post_url)
            time.sleep(5)

            # 关闭遮罩层
            dismiss_overlay(self.driver)
            time.sleep(2)

            # 再次检查是否还有遮罩层
            dismiss_overlay(self.driver)

            # 检测发送限制
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"评论被限制: {restriction_text}"

            # 关闭通知弹窗
            try:
                deny_buttons = self.driver.find_elements(By.XPATH,
                    "//button[contains(text(), '禁止') or contains(text(), 'Block')]")
                for button in deny_buttons:
                    if button.is_displayed():
                        button.click()
                        time.sleep(1)
                        break
            except Exception:
                pass

            self.driver.execute_script("window.scrollBy(0, 300);")
            time.sleep(2)

            # 点击评论按钮激活评论区
            try:
                comment_buttons = self.driver.find_elements(By.XPATH,
                    "//div[@aria-label='评论' or @aria-label='Comment' or @aria-label='Leave a comment']"
                )
                if not comment_buttons:
                    comment_buttons = self.driver.find_elements(By.XPATH,
                        "//span[text()='评论' or text()='Comment']//ancestor::div[@role='button']"
                    )
                if comment_buttons:
                    for button in comment_buttons:
                        if button.is_displayed():
                            self.driver.execute_script("arguments[0].click();", button)
                            time.sleep(3)
                            break
            except Exception:
                pass

            # 查找评论框
            comment_box = None
            comment_selectors = [
                "//div[(@aria-label='发表公开评论…' or @aria-label='输入回答…' or @aria-label='提交首条评论…' or @aria-label='写评论…' or @aria-label='Write a comment…' or @aria-label='Write a public comment…' or @aria-label='Write an answer…' or @aria-label='Submit the first comment…') and @contenteditable='true' and @role='textbox']",
                "//div[contains(@aria-label, '评论') and @contenteditable='true']",
                "//div[contains(@aria-label, 'comment') and @contenteditable='true']",
                "//div[@contenteditable='true' and @role='textbox']",
                "//form//div[@role='textbox']",
                "//div[@data-lexical-editor='true']",
            ]

            for selector in comment_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        if element.is_displayed():
                            comment_box = element
                            break
                    if comment_box:
                        break
                except Exception:
                    continue

            if not comment_box:
                # 重试：滚动页面、再次关闭遮罩层、再次点击评论按钮
                logger.info(f"[{self.account_name}] 未找到评论框，重试中...")
                dismiss_overlay(self.driver)
                self.driver.execute_script("window.scrollBy(0, 500);")
                time.sleep(2)
                try:
                    retry_buttons = self.driver.find_elements(By.XPATH,
                        "//div[@aria-label='评论' or @aria-label='Comment' or @aria-label='Leave a comment']"
                    )
                    for button in retry_buttons:
                        if button.is_displayed():
                            self.driver.execute_script("arguments[0].click();", button)
                            time.sleep(3)
                            break
                except Exception:
                    pass

                for selector in comment_selectors:
                    try:
                        elements = self.driver.find_elements(By.XPATH, selector)
                        for element in elements:
                            if element.is_displayed():
                                comment_box = element
                                break
                        if comment_box:
                            break
                    except Exception:
                        continue

            if not comment_box:
                return False, "未找到评论框"

            # 滚动到评论框并点击
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
            time.sleep(1)
            self.driver.execute_script("arguments[0].click();", comment_box)
            time.sleep(1)

            actions = ActionChains(self.driver)
            actions.move_to_element(comment_box).click().perform()
            time.sleep(1)

            self.driver.execute_script("arguments[0].innerHTML = '';", comment_box)

            # 逐字符输入（带JS兜底）
            try:
                for char in comment_text:
                    comment_box.send_keys(char)
                    time.sleep(random.uniform(0.05, 0.15))
            except Exception as input_err:
                logger.warning(f"[{self.account_name}] send_keys失败: {input_err}, 使用JS注入")
                try:
                    self.driver.execute_script("""
                        var el = arguments[0];
                        el.focus();
                        el.textContent = arguments[1];
                        var evt = new InputEvent('input', {bubbles: true, cancelable: true});
                        el.dispatchEvent(evt);
                    """, comment_box, comment_text)
                    time.sleep(1)
                except Exception as js_err:
                    logger.error(f"[{self.account_name}] JS注入也失败: {js_err}")
                    return False, f"输入评论失败: {js_err}"

            time.sleep(2)

            # 检测限制
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"评论被限制: {restriction_text}"

            # 发送评论
            comment_box.send_keys(Keys.ENTER)
            time.sleep(5)

            # 发送后检测
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"评论被限制: {restriction_text}"

            # 尝试点击发布按钮（多语言）
            try:
                post_buttons = self.driver.find_elements(By.XPATH,
                    "//div[@aria-label='发布' or @aria-label='Post' or @aria-label='Submit'"
                    " or @aria-label='Publier' or @aria-label='Publicar'"
                    " or @aria-label='Yayınla' or @aria-label='Đăng'"
                    " or @aria-label='Kirim' or @aria-label='نشر']"
                )
                for button in post_buttons:
                    if button.is_displayed():
                        self.driver.execute_script("arguments[0].click();", button)
                        time.sleep(5)
                        break
            except Exception:
                pass

            logger.info(f"[{self.account_name}] 评论发送成功")
            return True, "已评论"

        except Exception as e:
            logger.error(f"[{self.account_name}] 评论出错: {e}")
            return False, f"评论过程出错: {e}"

    def execute_dm(self, author_id, message_text):
        """发送私信"""
        if not self.initialized or not self.driver:
            return False, "发送引擎未初始化"

        user_url = f"https://www.facebook.com/{author_id}"
        logger.info(f"[{self.account_name}] 发送私信到: {user_url}")

        try:
            self.driver.get(user_url)
            time.sleep(3)

            # 关闭遮罩层
            dismiss_overlay(self.driver)

            # 检测限制
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"私信被限制: {restriction_text}"

            # 关闭已有聊天窗口（多语言）
            try:
                close_buttons = self.driver.find_elements(By.XPATH,
                    "//div[@aria-label='关闭聊天窗口' or @aria-label='Close chat'"
                    " or @aria-label='Fermer' or @aria-label='Cerrar'"
                    " or @aria-label='Fechar' or @aria-label='Kapat'"
                    " or @aria-label='Đóng' or @aria-label='Tutup'"
                    " or @aria-label='إغلاق']"
                )
                for button in close_buttons:
                    if button.is_displayed():
                        self.driver.execute_script("arguments[0].click();", button)
                        time.sleep(2)
                        break
            except Exception:
                pass

            # 点击发消息按钮（多语言）
            msg_btn_labels = [
                '发消息', 'Message', 'Envoyer un message',
                'Mensaje', 'Mensagem', 'Mesaj Gönder',
                'Nhắn tin', 'Pesan', 'رسالة',
            ]
            span_conds = " or ".join([f"text()='{lbl}'" for lbl in msg_btn_labels])
            message_buttons = self.driver.find_elements(By.XPATH, f"//span[{span_conds}]")
            if not message_buttons:
                div_conds = " or ".join([f"contains(text(), '{lbl}')" for lbl in msg_btn_labels])
                message_buttons = self.driver.find_elements(By.XPATH, f"//div[{div_conds}]")
            if not message_buttons:
                aria_conds = " or ".join([f"@aria-label='{lbl}'" for lbl in msg_btn_labels])
                message_buttons = self.driver.find_elements(By.XPATH, f"//div[{aria_conds}]")

            if not message_buttons:
                return False, "未找到发消息按钮"

            for button in message_buttons:
                if button.is_displayed():
                    self.driver.execute_script("arguments[0].click();", button)
                    time.sleep(3)
                    break

            time.sleep(3)

            # 检测限制
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"私信被限制: {restriction_text}"

            # 检测24小时消息限制
            if detect_message_rate_limit(self.driver):
                return False, "RATE_LIMITED:已达到24小时消息请求上限"

            # 查找消息输入框（多语言）
            message_input = None
            input_selectors = [
                "//div[@aria-label='发消息' and @contenteditable='true']",
                "//div[@aria-label='Message' and @contenteditable='true']",
                "//div[@aria-label='Aa' and @contenteditable='true']",
                "//div[@role='textbox' and @contenteditable='true']",
                "//div[@data-lexical-editor='true']",
            ]

            for selector in input_selectors:
                try:
                    msg_input = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    if msg_input.is_displayed():
                        message_input = msg_input
                        break
                except Exception:
                    continue

            if not message_input:
                for selector in input_selectors:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        if element.is_displayed():
                            message_input = element
                            break
                    if message_input:
                        break

            if not message_input:
                return False, "未找到消息输入框"

            # 输入消息
            self.driver.execute_script("arguments[0].scrollIntoView(true);", message_input)
            actions = ActionChains(self.driver)
            actions.move_to_element(message_input).click().perform()
            time.sleep(1)

            self.driver.execute_script("arguments[0].innerHTML = '';", message_input)
            time.sleep(1)

            # 逐字符输入（支持多行，带JS兜底）
            try:
                lines = message_text.split('\n')
                if lines:
                    for char in lines[0]:
                        actions = ActionChains(self.driver)
                        actions.send_keys(char).perform()
                        time.sleep(random.uniform(0.05, 0.15))

                for line in lines[1:]:
                    actions = ActionChains(self.driver)
                    actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
                    time.sleep(0.5)
                    for char in line:
                        actions = ActionChains(self.driver)
                        actions.send_keys(char).perform()
                        time.sleep(random.uniform(0.05, 0.15))
            except Exception as input_err:
                logger.warning(f"[{self.account_name}] DM send_keys失败: {input_err}, 使用JS注入")
                try:
                    # 将换行转为<br>用于contenteditable
                    html_text = message_text.replace('\n', '<br>')
                    self.driver.execute_script("""
                        var el = arguments[0];
                        el.focus();
                        el.innerHTML = arguments[1];
                        var evt = new InputEvent('input', {bubbles: true, cancelable: true});
                        el.dispatchEvent(evt);
                    """, message_input, html_text)
                    time.sleep(1)
                except Exception as js_err:
                    logger.error(f"[{self.account_name}] DM JS注入也失败: {js_err}")
                    return False, f"输入私信失败: {js_err}"

            time.sleep(2)

            # 发送前检测限制
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"私信被限制: {restriction_text}"

            # 点击发送按钮或按Enter（多语言）
            send_buttons = self.driver.find_elements(By.XPATH,
                "//div[@aria-label='按 Enter 键发送' or @aria-label='Press Enter to send'"
                " or @aria-label='Appuyez sur Entrée pour envoyer'"
                " or @aria-label='Presiona Enter para enviar'"
                " or @aria-label='Gönder' or @aria-label='Gửi'"
                " or @aria-label='Kirim' or @aria-label='إرسال']"
            )
            if send_buttons:
                for button in send_buttons:
                    if button.is_displayed():
                        self.driver.execute_script("arguments[0].click();", button)
                        break
                else:
                    message_input.send_keys(Keys.ENTER)
            else:
                message_input.send_keys(Keys.ENTER)

            time.sleep(5)

            # 发送后检测一般限制
            is_restricted, restriction_text = detect_sending_restriction(self.driver)
            if is_restricted:
                return False, f"私信被限制: {restriction_text}"

            # 专门检测24小时消息限制
            if detect_message_rate_limit(self.driver):
                return False, "RATE_LIMITED:已达到24小时消息请求上限"

            logger.info(f"[{self.account_name}] 私信发送成功")
            return True, "已私信"

        except Exception as e:
            logger.error(f"[{self.account_name}] 私信出错: {e}")
            return False, f"私信过程出错: {e}"

    def execute_add_friend(self, author_id):
        """添加好友"""
        if not self.initialized or not self.driver:
            return False, "发送引擎未初始化"

        user_url = f"https://www.facebook.com/{author_id}"
        logger.info(f"[{self.account_name}] 添加好友: {user_url}")

        try:
            # 检查当前页面是否已经是目标用户主页
            current_url = self.driver.current_url or ""
            if author_id not in current_url:
                self.driver.get(user_url)
                time.sleep(3)

            # 关闭遮罩层
            dismiss_overlay(self.driver)

            # 查找添加好友按钮（多语言）
            add_friend_labels = [
                '添加好友', '加为好友', 'Add Friend', 'Add friend',
                'Ajouter', 'Agregar',  # 法语/西班牙语
                'Adicionar',  # 葡萄牙语
                'Arkadaş Ekle',  # 土耳其语
                'Thêm bạn bè',  # 越南语
                'Tambah Teman',  # 印尼语
                'إضافة صديق',  # 阿拉伯语
            ]
            # 构建XPath: span精确匹配
            span_conditions = " or ".join([f"text()='{lbl}'" for lbl in add_friend_labels])
            add_friend_buttons = self.driver.find_elements(By.XPATH,
                f"//span[{span_conditions}]")
            # 兜底: div contains匹配
            if not add_friend_buttons:
                div_conditions = " or ".join([f"contains(text(), '{lbl}')" for lbl in add_friend_labels])
                add_friend_buttons = self.driver.find_elements(By.XPATH,
                    f"//div[{div_conditions}]")
            # 再兜底: aria-label匹配
            if not add_friend_buttons:
                aria_conditions = " or ".join([f"@aria-label='{lbl}'" for lbl in add_friend_labels])
                add_friend_buttons = self.driver.find_elements(By.XPATH,
                    f"//div[{aria_conditions}]")

            if add_friend_buttons:
                for button in add_friend_buttons:
                    if button.is_displayed():
                        self.driver.execute_script("arguments[0].click();", button)
                        time.sleep(3)
                        logger.info(f"[{self.account_name}] 已发送好友申请")
                        return True, "已发送申请"

            return False, "未找到添加好友按钮"

        except Exception as e:
            logger.error(f"[{self.account_name}] 添加好友出错: {e}")
            return False, f"添加好友出错: {e}"
