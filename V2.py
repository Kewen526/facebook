import time
import logging
import pyotp
import requests
from urllib.parse import urlparse, parse_qs
from urllib import parse
import json
import os
import tempfile
import random
import threading
import concurrent.futures
from pathlib import Path
import queue
import re
import subprocess
import platform
import socket
import zipfile
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, List, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============ 全局配置 ============
# API 配置
API_KEY_2CAPTCHA = "ec7a8c9f84930f5be81869f308aef0bc"

# 用户数据目录
USER_DATA_DIRS = {
    "monitor": os.path.join(os.path.expanduser("~"), "fb_automation_monitor_profile"),
    "sender": os.path.join(os.path.expanduser("~"), "fb_automation_sender_profile")
}

# 持久化存储文件 - 分离防重复机制
MONITORED_POSTS_FILE = os.path.join(os.path.expanduser("~"), "fb_monitored_posts.json")  # 监控模块专用
PROCESSED_TASKS_FILE = os.path.join(os.path.expanduser("~"), "fb_processed_tasks.json")  # 发送模块专用
MESSAGED_USERS_FILE = os.path.join(os.path.expanduser("~"), "fb_messaged_users.json")  # 用户级防重复
DRIVER_CACHE_DIR = os.path.join(tempfile.gettempdir(), "chrome_drivers")
COOKIES_DIR = os.path.join(os.path.expanduser("~"), "fb_cookies")

# 随机用户代理列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


# ============ API调用模块 ============
def get_team_detail(team_name):
    """获取团队详情"""
    try:
        print(f"正在获取团队详情: {team_name}")

        request_url = 'http://47.95.157.46:8520/api/team/detail'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        form_data = {"teamName": team_name}
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)

        if response.status_code == 200:
            result = response.json()
            if result.get("success") and result.get("data"):
                print(f"✅ 成功获取团队详情，共 {len(result['data'])} 个账号")
                return result["data"]
            else:
                print(f"❌ API返回失败: {result}")
                return None
        else:
            print(f"❌ API请求失败，状态码: {response.status_code}")
            return None

    except Exception as e:
        print(f"❌ 获取团队详情时出错: {e}")
        return None


def get_cookie_url_from_api(account):
    """从API获取cookie URL"""
    try:
        print(f"🌐 从API获取cookie URL for account: {account}")

        request_url = 'http://47.95.157.46:8520/api/cookie/url'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        form_data = {"account": account}
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)

        if response.status_code == 200:
            result = response.json()
            if result.get("success") and "data" in result and len(result["data"]) > 0:
                cookie_url = result["data"][0].get("cookie_url")
                if cookie_url:
                    print(f"✅ 成功获取cookie URL: {cookie_url}")
                    return cookie_url
                else:
                    print("❌ API响应中未找到cookie_url字段")
                    return None
            else:
                print(f"❌ API返回失败: {result.get('msg', '未知错误')}")
                return None
        else:
            print(f"❌ API请求失败，状态码: {response.status_code}")
            return None

    except Exception as e:
        print(f"❌ 获取cookie URL时出错: {e}")
        return None


def report_cookie_status(account, cookie_status):
    """向API报告cookie状态"""
    try:
        print(f"📡 报告cookie状态 - Account: {account}, Status: {cookie_status}")

        request_url = 'http://47.95.157.46:8520/api/cookie/report_status'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        form_data = {
            "account": account,
            "cookieStatus": cookie_status
        }
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)

        if response.status_code == 200:
            print("✅ 成功报告cookie状态")
            return True
        else:
            print(f"❌ 报告cookie状态失败")
            return False

    except Exception as e:
        print(f"❌ 报告cookie状态时出错: {e}")
        return False


def upload_to_cos(path, filename):
    """上传文件到腾讯云COS"""
    try:
        from qcloud_cos import CosConfig, CosS3Client

        # 配置腾讯云 COS 凭证
        secret_id = 'AKIDrYz93g26vUmpb6KHxMULvFI4aonVw60d'
        secret_key = 'OVMwFH1astc4FApEMCm47tOcaGfnfFXZ'
        region = 'ap-beijing'
        bucket = 'ceshi-1300392622'

        # 代理设置
        proxy = {'http': None, 'https': None}

        # 配置CosConfig
        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key, Token=None, Proxies=proxy)
        client = CosS3Client(config)

        # 上传文件
        response = client.put_object_from_local_file(
            Bucket=bucket,
            LocalFilePath=path,
            Key=filename
        )

        # 获取上传后的文件URL
        url = f"https://{bucket}.cos.{region}.myqcloud.com/{filename}"
        return url

    except Exception as e:
        print(f"文件上传失败: {e}")
        return None


def update_cookie_url(cookie_url, account):
    """更新cookie URL到API"""
    try:
        print(f"📡 准备更新cookie URL: {cookie_url}")
        print(f"📡 账号: {account}")

        request_url = 'http://47.95.157.46:8520/api/cookie/update'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        # 尝试不同的参数名格式
        form_data = {
            "cookieUrl": cookie_url,  # 改为驼峰命名
            "account": account
        }
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        print(f"📡 请求参数: cookieUrl={cookie_url}, account={account}")
        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)
        print(f"📡 更新cookie URL响应: {response.status_code} - {response.text}")

        if response.status_code == 200:
            try:
                result = response.json()
                if result.get("success"):
                    print("✅ Cookie URL更新成功")
                    return True
                else:
                    print(f"❌ Cookie URL更新失败: {result.get('msg', '未知错误')}")
                    return False
            except:
                # 如果响应不是JSON，但状态码是200，也认为成功
                print("✅ Cookie URL更新成功 (非JSON响应)")
                return True
        else:
            print(f"❌ Cookie URL更新失败，状态码: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ 更新cookie URL时出错: {e}")
        return False


def update_account_status(account, account_status):
    """更新账号状态到API"""
    try:
        print(f"📡 更新账号状态 - Account: {account}, Status: {account_status}")

        request_url = 'http://47.95.157.46:8520/api/accounts/status'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        form_data = {
            "account": account,
            "account_status": account_status,
            "account_status_update_time": current_time
        }
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)

        print(f"📡 账号状态更新响应: {response.status_code} - {response.text}")

        if response.status_code == 200:
            print(f"✅ 成功更新账号状态为: {account_status}")
            return True
        else:
            print(f"❌ 更新账号状态失败，状态码: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ 更新账号状态时出错: {e}")
        return False


def release_post_id(post_id, team_name):
    """释放帖子ID"""
    try:
        print(f"📡 释放帖子ID - Post ID: {post_id}, Team: {team_name}")

        request_url = 'http://47.95.157.46:8520/api/posts/send-status'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        form_data = {
            "post_id": post_id,
            "team_name": team_name
        }
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)

        print(f"📡 释放帖子ID响应: {response.status_code} - {response.text}")

        if response.status_code == 200:
            print(f"✅ 成功释放帖子ID: {post_id}")
            return True
        else:
            print(f"❌ 释放帖子ID失败，状态码: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ 释放帖子ID时出错: {e}")
        return False


def get_account_info(account):
    """获取账号信息和状态"""
    try:
        print(f"📡 获取账号信息 - Account: {account}")

        request_url = 'http://47.95.157.46:8520/api/account/info'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        form_data = {"account": account}
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=30)

        if response.status_code == 200:
            result = response.json()
            if result.get("success") and result.get("data"):
                account_data = result["data"][0]
                account_status = account_data.get("account_status", "")
                account_status_update_time = account_data.get("account_status_update_time")

                print(f"✅ 账号状态: {account_status}")
                print(f"✅ 状态更新时间: {account_status_update_time}")

                return {
                    "success": True,
                    "account_status": account_status,
                    "account_status_update_time": account_status_update_time,
                    "data": account_data
                }
            else:
                print(f"❌ 获取账号信息失败: {result}")
                return {"success": False, "error": result.get("msg", "未知错误")}
        else:
            print(f"❌ 获取账号信息失败，状态码: {response.status_code}")
            return {"success": False, "error": f"HTTP {response.status_code}"}

    except Exception as e:
        print(f"❌ 获取账号信息时出错: {e}")
        return {"success": False, "error": str(e)}


def check_account_can_send(account):
    """检查账号是否可以执行发送任务"""
    try:
        account_info = get_account_info(account)

        if not account_info["success"]:
            print(f"❌ 无法获取账号信息，默认允许发送")
            return True

        account_status = account_info.get("account_status", "")
        account_status_update_time = account_info.get("account_status_update_time")

        # 如果状态是正常或空值，允许发送
        if account_status == "正常" or not account_status:
            print(f"✅ 账号状态正常，允许发送任务")
            return True

        # 如果状态是封禁，检查时间间隔
        if account_status == "封禁":
            if not account_status_update_time:
                print(f"⚠️ 账号被封禁但没有更新时间，默认允许发送")
                return True

            try:
                # 将时间戳转换为datetime对象
                if isinstance(account_status_update_time, (int, float)):
                    # 如果是时间戳（毫秒）
                    if account_status_update_time > 10 ** 10:
                        update_time = datetime.fromtimestamp(account_status_update_time / 1000)
                    else:
                        update_time = datetime.fromtimestamp(account_status_update_time)
                else:
                    # 如果是字符串格式的时间
                    update_time = datetime.strptime(str(account_status_update_time), '%Y-%m-%d %H:%M:%S')

                current_time = datetime.now()
                time_diff = current_time - update_time
                hours_diff = time_diff.total_seconds() / 3600

                print(f"📊 封禁时间: {update_time}")
                print(f"📊 当前时间: {current_time}")
                print(f"📊 时间间隔: {hours_diff:.2f} 小时")

                if hours_diff > 10:
                    print(f"✅ 封禁超过10小时，允许发送任务")
                    return True
                else:
                    print(f"❌ 封禁未超过10小时，禁止发送任务")
                    return False

            except Exception as e:
                print(f"⚠️ 解析封禁时间失败: {e}，默认允许发送")
                return True

        # 其他状态默认允许发送
        print(f"⚠️ 未知账号状态: {account_status}，默认允许发送")
        return True

    except Exception as e:
        print(f"❌ 检查账号状态时出错: {e}，默认允许发送")
        return True


def detect_sending_restriction(driver):
    """检测是否遇到发送限制"""
    try:
        # 检测各种限制提示
        restriction_selectors = [
            # 你暂时无法使用这项功能
            "//span[contains(text(), '你暂时无法使用这项功能')]",
            "//span[contains(text(), 'temporarily unable to use this feature')]",
            # 陌生消息上限
            "//span[contains(text(), '你已达到陌生消息数量上限')]",
            "//span[contains(text(), '陌生消息已达到上限')]",
            "//span[contains(text(), 'stranger message limit')]",
            # 发送频率限制
            "//span[contains(text(), '发送频率过快')]",
            "//span[contains(text(), '请稍后再试')]",
            "//span[contains(text(), 'try again later')]",
            # 评论限制
            "//span[contains(text(), '评论功能暂时不可用')]",
            "//span[contains(text(), 'commenting is temporarily unavailable')]",
            # 其他常见限制
            "//span[contains(text(), '操作过于频繁')]",
            "//span[contains(text(), '暂时限制')]",
            "//span[contains(text(), 'temporarily restricted')]",
            "//span[contains(text(), 'rate limited')]"
        ]

        for selector in restriction_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed():
                        restriction_text = element.text.strip()
                        print(f"🚫 检测到发送限制: {restriction_text}")
                        return True, restriction_text
            except Exception as e:
                continue

        return False, None

    except Exception as e:
        print(f"❌ 检测发送限制时出错: {e}")
        return False, None


# ============ 登录模块 ============
def request_recaptcha_solution(sitekey: str, page_url: str, timeout: int = 120) -> str:
    """提交到 2Captcha 并轮询返回 g-recaptcha-response"""
    try:
        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        resp = requests.get(
            "http://2captcha.com/in.php",
            params={
                "key": API_KEY_2CAPTCHA,
                "method": "userrecaptcha",
                "googlekey": sitekey,
                "pageurl": page_url,
                "json": 1,
            },
            proxies=proxies,
            timeout=30
        ).json()

        if resp.get("status") != 1:
            logging.error(f"2Captcha 提交失败: {resp}")
            return None

        task_id = resp["request"]
        logging.info(f"2Captcha 任务已提交，ID={task_id}，开始轮询…")

        for _ in range(int(timeout / 5)):
            time.sleep(5)
            res = requests.get(
                "http://2captcha.com/res.php",
                params={
                    "key": API_KEY_2CAPTCHA,
                    "action": "get",
                    "id": task_id,
                    "json": 1
                },
                proxies=proxies,
                timeout=30
            ).json()

            if res.get("status") == 1:
                logging.info("2Captcha 返回 token")
                return res["request"]
            if res.get("request") != "CAPCHA_NOT_READY":
                logging.error(f"2Captcha 错误返回：{res}")
                return None

        logging.error("2Captcha 轮询超时")
        return None

    except Exception as e:
        logging.error(f"2Captcha 处理失败: {e}")
        return None


def get_sitekey(driver) -> str:
    """从页面中提取sitekey"""
    try:
        logging.info("开始获取sitekey...")

        # 方法1：从anchor iframe获取
        anchor_selectors = [
            "iframe[src*='api2/anchor']",
            "iframe[src*='recaptcha']",
            "iframe[src*='google.com/recaptcha']",
            "iframe#captcha-recaptcha"
        ]

        for i, selector in enumerate(anchor_selectors):
            try:
                anchor_iframes = driver.find_elements(By.CSS_SELECTOR, selector)
                if anchor_iframes:
                    for iframe in anchor_iframes:
                        anchor_src = iframe.get_attribute("src")
                        if anchor_src:
                            parsed_url = urlparse(anchor_src)
                            query_params = parse_qs(parsed_url.query)
                            sitekey = query_params.get("k", [None])[0]
                            if sitekey:
                                logging.info(f"从iframe获取到sitekey: {sitekey[:15]}...")
                                return sitekey
            except Exception as e:
                logging.debug(f"选择器 {selector} 处理失败: {e}")
                continue

        # 方法2：从页面元素的data-sitekey属性获取
        try:
            sitekey_elements = driver.find_elements(By.CSS_SELECTOR, "[data-sitekey]")
            if sitekey_elements:
                sitekey = sitekey_elements[0].get_attribute("data-sitekey")
                if sitekey:
                    logging.info(f"从data-sitekey获取到: {sitekey[:15]}...")
                    return sitekey
        except Exception as e:
            logging.debug(f"从data-sitekey获取失败: {e}")

        # 方法3：从页面JavaScript变量中获取
        try:
            sitekey = driver.execute_script("""
              // 查找页面中的grecaptcha配置
              if (window.grecaptcha) {
                  try {
                      if (window.grecaptcha.enterprise && window.grecaptcha.enterprise.getParameter) {
                          return window.grecaptcha.enterprise.getParameter('sitekey');
                      }
                  } catch(e) {}
              }

              // 查找全局变量中的sitekey
              for (let prop in window) {
                  try {
                      if (typeof window[prop] === 'object' && window[prop] && 
                          window[prop].sitekey && typeof window[prop].sitekey === 'string') {
                          return window[prop].sitekey;
                      }
                  } catch(e) {}
              }

              // 查找页面源码中的sitekey模式
              let pageText = document.documentElement.innerHTML;
              let sitekeyMatch = pageText.match(/sitekey['":\s]*([a-zA-Z0-9_-]{40})/i);
              if (sitekeyMatch && sitekeyMatch[1]) {
                  return sitekeyMatch[1];
              }

              return null;
          """)
            if sitekey:
                logging.info(f"从JavaScript获取到sitekey: {sitekey[:15]}...")
                return sitekey
        except Exception as e:
            logging.debug(f"从JavaScript获取sitekey失败: {e}")

        logging.error("所有方法都无法获取sitekey")
        return None

    except Exception as e:
        logging.error(f"获取sitekey时出错: {e}")
        return None


def inject_recaptcha_token(driver, token: str) -> bool:
    """注入reCAPTCHA token"""
    try:
        logging.info("开始注入 g-recaptcha-response token...")

        js_inject = """
      // 查找或创建 g-recaptcha-response textarea
      let ta = document.querySelector('textarea[name="g-recaptcha-response"]');
      if (!ta) {
          ta = document.createElement('textarea');
          ta.name = 'g-recaptcha-response';
          ta.id = 'g-recaptcha-response';
          ta.className = 'g-recaptcha-response';
          ta.style.width = '250px';
          ta.style.height = '40px';
          ta.style.border = '1px solid rgb(193, 193, 193)';
          ta.style.margin = '10px 25px';
          ta.style.padding = '0px';
          ta.style.resize = 'none';
          ta.style.display = 'none';

          let targetElements = [
              document.querySelector('form'),
              document.querySelector('.g-recaptcha'),
              document.querySelector('[data-sitekey]'),
              document.body
          ];

          for (let target of targetElements) {
              if (target) {
                  target.appendChild(ta);
                  break;
              }
          }
      }

      ta.value = arguments[0];

      let events = ['input', 'change', 'blur', 'focus', 'keyup', 'paste'];
      events.forEach(eventType => {
          try {
              let event = new Event(eventType, { 
                  bubbles: true, 
                  cancelable: true 
              });
              ta.dispatchEvent(event);
          } catch(e) {
              try {
                  let event = document.createEvent('HTMLEvents');
                  event.initEvent(eventType, true, true);
                  ta.dispatchEvent(event);
              } catch(e2) {}
          }
      });

      return {
          success: true,
          textareaFound: !!document.querySelector('textarea[name="g-recaptcha-response"]'),
          textareaValue: ta.value,
          textareaLength: ta.value ? ta.value.length : 0
      };
      """

        result = driver.execute_script(js_inject, token)

        if result and result.get('success'):
            logging.info(f"Token注入信息: textarea找到={result.get('textareaFound')}, "
                         f"token长度={result.get('textareaLength')}")
            time.sleep(3)

            # 验证token是否仍然存在
            verification_js = """
              let ta = document.querySelector('textarea[name="g-recaptcha-response"]');
              return {
                  exists: !!ta,
                  value: ta ? ta.value : null,
                  valueLength: ta && ta.value ? ta.value.length : 0,
                  isVisible: ta ? ta.style.display !== 'none' : false
              };
          """

            verification = driver.execute_script(verification_js)
            if verification and verification.get('valueLength', 0) > 10:
                logging.info("Token注入并验证成功")
                return True
            else:
                logging.warning("Token注入后验证失败")
                return False
        else:
            logging.error("Token注入脚本执行失败")
            return False

    except Exception as e:
        logging.error(f"Token注入失败: {e}")
        return False


def handle_challenge_iframe(driver, wait) -> bool:
    """处理challenge iframe和token注入"""
    driver.switch_to.default_content()

    logging.info("检查是否出现验证挑战...")
    time.sleep(3)

    try:
        challenge_selectors = [
            "iframe[src*='api2/bframe']",
            "iframe[src*='bframe']",
            "iframe[src*='recaptcha']",
            "iframe[title*='recaptcha']",
            "iframe[name*='c-']"
        ]

        challenge_found = False
        for selector in challenge_selectors:
            try:
                challenge_iframes = driver.find_elements(By.CSS_SELECTOR, selector)
                if challenge_iframes:
                    wait.until(EC.frame_to_be_available_and_switch_to_it((By.CSS_SELECTOR, selector)))
                    logging.info(f"成功切入challenge iframe: {selector}")
                    challenge_found = True
                    break
            except Exception as e:
                continue

        if not challenge_found:
            logging.info("未检测到challenge iframe，验证可能已通过或无需验证")
            return True

        driver.switch_to.default_content()

    except Exception as e:
        logging.info(f"challenge iframe处理过程出错: {e}")
        driver.switch_to.default_content()
        return True

    # 获取sitekey
    sitekey = get_sitekey(driver)
    if not sitekey:
        logging.error("无法获取sitekey，尝试继续...")
        return True

    logging.info(f"获取到sitekey: {sitekey[:10]}...")

    # 请求2Captcha解决方案
    token = request_recaptcha_solution(sitekey, driver.current_url)
    if not token:
        logging.error("2Captcha 未返回 token，尝试继续...")
        return True

    # 注入token
    success = inject_recaptcha_token(driver, token)
    if success:
        logging.info("Token注入成功，等待验证完成...")
        time.sleep(3)

    return success


def detect_verification_type(driver, wait) -> str:
    """检测当前页面的验证类型"""
    logging.info("正在检测验证类型...")
    time.sleep(3)

    current_url = driver.current_url.lower()
    logging.info(f"当前URL: {current_url}")

    # 优先检测设备信任页面
    if 'remember_browser' in current_url or 'trust' in current_url or 'save_device' in current_url:
        logging.info("通过URL检测到设备信任页面")
        return 'device_trust'

    # 检查设备信任页面的文本内容
    device_trust_indicators = [
        "//text()[contains(., '是否要信任这台设备')]",
        "//text()[contains(., '信任这台设备')]",
        "//text()[contains(., '保存这台设备')]",
        "//text()[contains(., 'Trust this device')]",
        "//text()[contains(., 'Save this device')]",
        "//text()[contains(., '你已登录')]",
        "//text()[contains(., 'You are logged in')]",
        "//button[contains(text(), '保存这台设备')]",
        "//button[contains(text(), 'Save this device')]",
    ]

    for indicator in device_trust_indicators:
        try:
            elements = driver.find_elements(By.XPATH, indicator)
            if elements:
                logging.info(f"检测到设备信任页面: {indicator}")
                return 'device_trust'
        except Exception as e:
            continue

    # 检查人机验证码
    recaptcha_indicators = [
        "iframe#captcha-recaptcha",
        "iframe[src*='recaptcha']",
        ".g-recaptcha",
        "[data-sitekey]",
        "#recaptcha-anchor"
    ]

    for indicator in recaptcha_indicators:
        if driver.find_elements(By.CSS_SELECTOR, indicator):
            logging.info(f"检测到人机验证码: {indicator}")
            return 'recaptcha'

    # 检查身份验证器页面
    if 'remember_browser' not in current_url:
        auth_app_indicators = [
            "//text()[contains(., '身份验证应用')]",
            "//text()[contains(., '验证码')]",
            "//text()[contains(., '6位数')]",
            "//text()[contains(., '身份验证器')]",
            "//text()[contains(., 'authentication app')]",
            "//text()[contains(., 'authenticator')]",
            "//text()[contains(., '6-digit')]",
            "//text()[contains(., 'verification code')]",
            "input[placeholder*='验证码']",
            "input[placeholder*='code']",
            "input[maxlength='6']",
            "[data-testid*='code']",
            "input[type='text'][id*='_r_']",
            "input[autocomplete='off'][type='text']"
        ]

        for indicator in auth_app_indicators:
            try:
                if indicator.startswith("//text()"):
                    elements = driver.find_elements(By.XPATH, indicator)
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, indicator)

                if elements:
                    logging.info(f"检测到身份验证器页面: {indicator}")
                    return '2fa_app'
            except Exception as e:
                continue

    # 检查SMS验证码
    sms_indicators = [
        "//text()[contains(., '短信')]",
        "//text()[contains(., 'SMS')]",
        "//text()[contains(., '手机')]",
        "//text()[contains(., 'phone')]",
        "#approvals_code"
    ]

    for indicator in sms_indicators:
        try:
            if indicator.startswith("//text()"):
                elements = driver.find_elements(By.XPATH, indicator)
            else:
                elements = driver.find_elements(By.CSS_SELECTOR, indicator)

            if elements:
                logging.info(f"检测到SMS验证码: {indicator}")
                return '2fa_sms'
        except:
            continue

    # 检查是否已经登录成功
    verification_url_patterns = ['checkpoint', 'login', 'two_factor', 'remember_browser', 'verify']
    is_verification_page = any(pattern in current_url for pattern in verification_url_patterns)

    if not is_verification_page:
        success_result = check_login_success(driver, wait)
        if success_result:
            logging.info("检测到已登录成功")
            return 'success'

    logging.warning("无法确定验证类型，返回unknown")
    return 'unknown'


def handle_2fa_app_verification(driver, wait, totp_secret: str) -> bool:
    """处理身份验证器/2FA应用验证"""
    logging.info("开始处理身份验证器验证...")

    try:
        # 生成TOTP验证码
        code = pyotp.TOTP(totp_secret.replace(" ", "")).now()
        logging.info(f"生成的验证码: {code}")

        # 查找验证码输入框
        code_input_selectors = [
            "input[placeholder*='验证码']",
            "input[placeholder*='code']",
            "input[maxlength='6']",
            "input[type='text'][name*='code']",
            "input[data-testid*='code']",
            "input[aria-label*='code']",
            "input[aria-label*='验证码']",
            "input[type='text']",
            "input[type='number']"
        ]

        code_input = None
        for selector in code_input_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        code_input = element
                        logging.info(f"找到验证码输入框: {selector}")
                        break
                if code_input:
                    break
            except Exception as e:
                logging.debug(f"选择器 {selector} 失败: {e}")
                continue

        if not code_input:
            logging.error("无法找到验证码输入框")
            return False

        # 清空并输入验证码
        code_input.clear()
        time.sleep(0.5)

        # 模拟人类输入
        for char in code:
            code_input.send_keys(char)
            time.sleep(0.2)

        time.sleep(1)

        # 查找并点击提交按钮
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "//button[contains(text(), '继续')]",
            "//button[contains(text(), 'Continue')]",
            "//button[contains(text(), '确认')]",
            "//button[contains(text(), 'Confirm')]",
            "[data-testid*='submit']",
            "[data-testid*='continue']",
            ".btn-primary",
            "button.primary"
        ]

        submit_clicked = False
        for selector in submit_selectors:
            try:
                if selector.startswith("//button"):
                    button = driver.find_element(By.XPATH, selector)
                else:
                    button = driver.find_element(By.CSS_SELECTOR, selector)

                if button.is_displayed() and button.is_enabled():
                    button.click()
                    logging.info(f"点击提交按钮: {selector}")
                    submit_clicked = True
                    break
            except Exception as e:
                logging.debug(f"提交按钮 {selector} 失败: {e}")
                continue

        # 如果没找到按钮，尝试按回车
        if not submit_clicked:
            logging.info("未找到提交按钮，尝试按回车")
            code_input.send_keys(Keys.ENTER)
            submit_clicked = True

        if submit_clicked:
            time.sleep(5)
            logging.info("2FA验证码已提交")
            return True
        else:
            logging.error("无法提交验证码")
            return False

    except Exception as e:
        logging.error(f"处理身份验证器验证失败: {e}")
        return False


def handle_device_trust_page(driver, wait) -> bool:
    """处理设备信任页面"""
    logging.info("检查是否出现设备信任页面...")

    try:
        time.sleep(3)

        # 检测设备信任页面的关键词
        trust_indicators = [
            "//text()[contains(., '是否要信任这台设备')]",
            "//text()[contains(., '信任这台设备')]",
            "//text()[contains(., '保存这台设备')]",
            "//text()[contains(., 'Trust this device')]",
            "//text()[contains(., 'Save this device')]",
            "//text()[contains(., '你已登录')]",
            "//text()[contains(., 'You are logged in')]",
        ]

        # 通过URL检测
        current_url = driver.current_url.lower()
        if 'remember_browser' in current_url:
            logging.info("通过URL检测到设备信任页面")
            trust_page_detected = True
        else:
            trust_page_detected = False
            for indicator in trust_indicators:
                try:
                    elements = driver.find_elements(By.XPATH, indicator)
                    if elements:
                        logging.info(f"检测到设备信任页面: {indicator}")
                        trust_page_detected = True
                        break
                except:
                    continue

        if not trust_page_detected:
            logging.info("未检测到设备信任页面")
            return True

        # 使用JavaScript脚本精确查找并点击"信任这台设备"按钮
        trust_button_script = """
          function findAndClickTrustButton() {
              let allElements = document.querySelectorAll('*');
              let candidates = [];

              for (let element of allElements) {
                  let text = (element.textContent || element.innerText || '').trim();

                  if (text === '信任这台设备' || text === 'Trust this device') {
                      let tagName = element.tagName.toLowerCase();
                      let role = element.getAttribute('role') || '';
                      let rect = element.getBoundingClientRect();
                      let isVisible = element.offsetParent !== null;

                      if (isVisible) {
                          let priority = 0;

                          if (role === 'button') priority += 1000;
                          if (rect.width > 500 && rect.height > 40) priority += 100;
                          if (rect.width > 100) priority += 10;

                          candidates.push({
                              element: element,
                              priority: priority,
                              role: role,
                              width: rect.width,
                              height: rect.height
                          });
                      }
                  }
              }

              candidates.sort((a, b) => b.priority - a.priority);

              if (candidates.length > 0) {
                  try {
                      let bestCandidate = candidates[0];
                      bestCandidate.element.click();
                      return {
                          success: true, 
                          text: '信任这台设备',
                          role: bestCandidate.role,
                          priority: bestCandidate.priority
                      };
                  } catch(e) {
                      try {
                          let event = new MouseEvent('click', {
                              view: window,
                              bubbles: true,
                              cancelable: true
                          });
                          candidates[0].element.dispatchEvent(event);
                          return {success: true, text: '信任这台设备', method: 'event'};
                      } catch(e2) {
                          return {success: false, error: e2.message};
                      }
                  }
              }
              return {success: false, candidatesCount: candidates.length};
          }

          return findAndClickTrustButton();
      """

        click_result = driver.execute_script(trust_button_script)

        if click_result and click_result.get('success'):
            logging.info(f"JavaScript成功点击信任按钮: {click_result}")
            time.sleep(5)

            # 检查页面是否跳转
            new_url = driver.current_url.lower()
            if 'remember_browser' not in new_url:
                logging.info("页面已跳转，设备信任处理成功")
                return True
            else:
                time.sleep(10)
                final_url = driver.current_url.lower()
                if 'remember_browser' not in final_url:
                    logging.info("延迟跳转成功")
                    return True

        logging.info("设备信任页面处理完成")
        return True

    except Exception as e:
        logging.error(f"处理设备信任页面失败: {e}")
        return True


def handle_post_login_verification(driver, wait, totp_secret: str) -> bool:
    """处理登录后的各种验证"""
    logging.info("开始处理登录后验证...")

    max_attempts = 3
    for attempt in range(max_attempts):
        logging.info(f"验证检测尝试 {attempt + 1}/{max_attempts}")

        verification_type = detect_verification_type(driver, wait)
        logging.info(f"检测到验证类型: {verification_type}")

        if verification_type == 'success':
            logging.info("登录已成功，无需进一步验证")
            return True

        elif verification_type == 'recaptcha':
            logging.info("处理人机验证码...")
            result = solve_recaptcha(driver, wait)
            if result:
                logging.info("人机验证处理完成")
                time.sleep(3)
                continue
            else:
                logging.warning("人机验证处理失败，但继续尝试")
                time.sleep(2)
                continue

        elif verification_type == '2fa_app':
            logging.info("处理身份验证器验证...")
            result = handle_2fa_app_verification(driver, wait, totp_secret)
            if result:
                logging.info("身份验证器验证完成")
                time.sleep(3)
                device_trust_result = handle_device_trust_page(driver, wait)
                if device_trust_result:
                    logging.info("设备信任页面处理完成")
                continue
            else:
                logging.error("身份验证器验证失败")
                return False

        elif verification_type == '2fa_sms':
            logging.info("检测到SMS验证，尝试作为2FA应用处理...")
            result = handle_2fa_app_verification(driver, wait, totp_secret)
            if result:
                time.sleep(3)
                device_trust_result = handle_device_trust_page(driver, wait)
                if device_trust_result:
                    logging.info("设备信任页面处理完成")
                continue
            else:
                logging.error("SMS验证处理失败")
                return False

        elif verification_type == 'device_trust':
            logging.info("检测到设备信任页面，处理中...")
            result = handle_device_trust_page(driver, wait)
            if result:
                logging.info("设备信任页面处理完成")
                time.sleep(3)
                continue
            else:
                logging.warning("设备信任页面处理失败，但继续")
                time.sleep(2)
                continue

        elif verification_type == 'unknown':
            logging.warning(f"未知验证类型，尝试 {attempt + 1}")
            time.sleep(3)

            device_trust_result = handle_device_trust_page(driver, wait)
            if device_trust_result:
                logging.info("可能处理了设备信任页面")

            if attempt == max_attempts - 1:
                logging.info("最后一次尝试，使用通用方法...")
                try:
                    handle_2fa_app_verification(driver, wait, totp_secret)
                    time.sleep(3)
                except:
                    pass
                try:
                    solve_recaptcha(driver, wait)
                    time.sleep(3)
                except:
                    pass
                break

        time.sleep(2)

    # 最终检查登录状态
    final_check = check_login_success(driver, wait)
    if final_check:
        logging.info("验证处理完成，登录成功")
        return True
    else:
        logging.error("验证处理完成，但登录状态未确认")
        return False


def solve_recaptcha(driver, wait) -> bool:
    """处理reCAPTCHA验证"""
    logging.info("尝试处理人机验证…")

    try:
        # 检查是否存在验证码iframe
        captcha_iframes = driver.find_elements(By.CSS_SELECTOR, "iframe#captcha-recaptcha")
        if not captcha_iframes:
            logging.info("未检测到验证码iframe，可能不需要验证或页面结构已变化")
            return True

        wait.until(EC.frame_to_be_available_and_switch_to_it(
            (By.CSS_SELECTOR, "iframe#captcha-recaptcha")))
        logging.info("切入 Facebook captcha-recaptcha iframe")
    except Exception as e:
        logging.info(f"未能切入验证码iframe: {e}")
        return True

    clicked = False
    cb = None

    # 查找验证码checkbox
    checkbox_selectors = [
        "#recaptcha-anchor",
        ".recaptcha-checkbox",
        ".rc-anchor-checkbox",
        "span[role='checkbox']",
        ".recaptcha-checkbox-unchecked",
        "[aria-checked='false']"
    ]

    logging.info("开始查找验证码checkbox元素...")
    time.sleep(3)

    for i, selector in enumerate(checkbox_selectors):
        try:
            logging.info(f"尝试选择器 {i + 1}/{len(checkbox_selectors)}: {selector}")
            cb = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            logging.info(f"使用选择器 {selector} 找到checkbox元素")
            break
        except Exception as e:
            logging.warning(f"选择器 {selector} 失败: {e}")
            continue

    if cb is None:
        logging.error("所有选择器都失败，无法找到checkbox")
        driver.switch_to.default_content()
        return handle_challenge_iframe(driver, wait)

    # 尝试点击checkbox
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", cb)
        time.sleep(1)

        WebDriverWait(driver, 10).until(EC.visibility_of(cb))

        try:
            cb.click()
            clicked = True
            logging.info("常规 click 成功")
        except Exception as err1:
            logging.warning(f"常规 click 失败：{err1}")
            try:
                ActionChains(driver).move_to_element(cb).pause(0.5).click().perform()
                clicked = True
                logging.info("ActionChains click 成功")
            except Exception as err2:
                logging.warning(f"ActionChains click 失败：{err2}")
                try:
                    driver.execute_script("arguments[0].click();", cb)
                    clicked = True
                    logging.info("JS click 成功")
                except Exception as err3:
                    logging.error(f"所有点击方式都失败：{err3}")

        if clicked:
            time.sleep(2)

    except Exception as e:
        logging.error(f"点击过程中发生错误：{e}")

    driver.switch_to.default_content()
    time.sleep(2)

    return handle_challenge_iframe(driver, wait)


def check_login_success(driver, wait) -> bool:
    """检查登录是否成功"""
    logging.info("开始验证登录状态...")

    current_url = driver.current_url.lower()
    verification_url_patterns = [
        'checkpoint',
        'login_approval',
        'two_factor',
        'verify',
        'security_check'
    ]

    # 对于设备信任页面，先尝试处理
    if 'remember_browser' in current_url:
        logging.info("检测到设备信任页面，尝试最后处理...")
        handle_device_trust_page(driver, wait)
        time.sleep(5)
        current_url = driver.current_url.lower()

    # 检查其他验证页面模式
    for pattern in verification_url_patterns:
        if pattern in current_url:
            logging.info(f"检测到验证页面URL模式: {pattern}，不是登录成功")
            return False

    # 登录成功指示器
    success_indicators = [
        "nav[role='navigation']",
        "div[role='navigation']",
        "header[role='banner']",
        "[aria-label*='账号'][role='button']",
        "[aria-label*='Account'][role='button']",
        "[aria-label*='Profile'][role='button']",
        "[data-testid='user-menu']",
        "[data-testid='user_menu']",
        "[aria-label*='主页']",
        "[aria-label*='Home']",
        "div[role='complementary']",
        "div[role='main']",
        "div[role='feed']",
        "input[placeholder*='搜索']",
        "input[placeholder*='Search']",
        "[data-pagelet]",
    ]

    success_count = 0
    found_indicators = []

    for indicator in success_indicators:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, indicator)
            if elements:
                visible_elements = [elem for elem in elements if elem.is_displayed()]
                if visible_elements:
                    logging.info(f"通过指示器 '{indicator}' 检测到登录成功 (找到{len(visible_elements)}个可见元素)")
                    success_count += 1
                    found_indicators.append(indicator)
        except Exception as e:
            logging.debug(f"指示器 '{indicator}' 检查失败: {e}")
            continue

    logging.info(f"总共找到 {success_count} 个登录成功指示器")

    if success_count >= 1:
        logging.info(f"找到 {success_count} 个登录成功指示器，确认登录成功")
        return True

    # 检查是否存在登录表单
    login_form_indicators = [
        "#email", "[name='email']",
        "#loginbutton", "[name='login']",
        "input[type='password'][name='pass']"
    ]

    has_login_form = False
    for indicator in login_form_indicators:
        elements = driver.find_elements(By.CSS_SELECTOR, indicator)
        if elements:
            visible_elements = [elem for elem in elements if elem.is_displayed()]
            if visible_elements:
                has_login_form = True
                logging.info(f"检测到登录表单: {indicator}")
                break

    if has_login_form:
        logging.info("检测到登录表单，确认未登录")
        return False

    if not has_login_form:
        if success_count >= 1:
            logging.info("未检测到登录表单且有成功指示器，确认登录成功")
            return True
        elif current_url.startswith("https://www.facebook.com") and not current_url.endswith("login"):
            verification_inputs = driver.find_elements(By.CSS_SELECTOR,
                                                       "input[type='text'][maxlength='6'], input[placeholder*='code'], input[placeholder*='验证码'], input[name*='code']")
            visible_verification = [inp for inp in verification_inputs if inp.is_displayed()]

            if not visible_verification:
                logging.info("在Facebook主页且无验证输入框，确认登录成功")
                return True

    logging.warning(f"登录验证失败，成功指示器数量: {success_count}, URL: {current_url}")
    return False


def ensure_facebook_login(email: str, password: str, totp_secret: str, headless: bool = False) -> webdriver.Chrome:
    """确保Facebook登录成功"""
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)

    if headless:
        opts.add_argument("--headless")

    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")

    driver = webdriver.Chrome(options=opts)

    try:
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
              Object.defineProperty(navigator, 'webdriver', {
                  get: () => undefined,
              });
              Object.defineProperty(navigator, 'plugins', {
                  get: () => [1, 2, 3, 4, 5],
              });
              Object.defineProperty(navigator, 'languages', {
                  get: () => ['en-US', 'en'],
              });
              window.chrome = {
                  runtime: {}
              };
          '''
        })
    except Exception as e:
        logging.debug(f"CDP命令执行失败: {e}")

    wait = WebDriverWait(driver, 30)

    try:
        logging.info("访问Facebook登录页面")
        driver.get("https://www.facebook.com/")
        time.sleep(5)

        # 检查是否已登录
        login_indicators = ["#email", "[name='email']", "input[type='email']"]
        already_logged_in = True

        for indicator in login_indicators:
            if driver.find_elements(By.CSS_SELECTOR, indicator):
                already_logged_in = False
                break

        if already_logged_in:
            logging.info("已检测到登录状态")
            return driver

        logging.info("未登录，输入账号密码")

        # 找到登录元素
        email_input = None
        password_input = None
        login_button = None

        email_selectors = ["#email", "[name='email']", "input[type='email']", "[data-testid='royal_email']"]
        password_selectors = ["#pass", "[name='pass']", "input[type='password']", "[data-testid='royal_pass']"]
        button_selectors = ["[name='login']", "[type='submit']", "[data-testid='royal_login_button']",
                            "button[type='submit']"]

        for selector in email_selectors:
            try:
                email_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
                logging.info(f"找到邮箱输入框: {selector}")
                break
            except:
                continue

        for selector in password_selectors:
            try:
                password_input = driver.find_element(By.CSS_SELECTOR, selector)
                logging.info(f"找到密码输入框: {selector}")
                break
            except:
                continue

        for selector in button_selectors:
            try:
                login_button = driver.find_element(By.CSS_SELECTOR, selector)
                logging.info(f"找到登录按钮: {selector}")
                break
            except:
                continue

        if not email_input or not password_input or not login_button:
            logging.error("无法找到登录表单元素")
            driver.quit()
            return None

        # 模拟人类输入
        email_input.clear()
        time.sleep(1)
        for char in email:
            email_input.send_keys(char)
            time.sleep(0.1)
        time.sleep(1)

        password_input.clear()
        time.sleep(1)
        for char in password:
            password_input.send_keys(char)
            time.sleep(0.1)
        time.sleep(2)

        login_button.click()
        logging.info("已点击登录按钮")
        time.sleep(5)

        # 处理登录后的各种验证
        verification_result = handle_post_login_verification(driver, wait, totp_secret)
        if not verification_result:
            logging.error("登录后验证处理失败")
        else:
            logging.info("登录后验证处理完成")

        time.sleep(5)

        # 检查登录成功
        login_success = check_login_success(driver, wait)

        if login_success:
            logging.info("Facebook 自动登录成功")
            return driver
        else:
            logging.error("登录验证失败")
            driver.quit()
            return None

    except Exception as e:
        logging.error(f"登录过程中发生错误：{e}")
        driver.quit()
        return None


def save_cookies_to_file(driver, account):
    """保存cookies到文件"""
    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)

        cookies_data = {
            'cookies': driver.get_cookies(),
            'local_storage': {}
        }

        # 获取localStorage数据
        try:
            local_storage_script = """
              var items = {};
              for (var i = 0; i < localStorage.length; i++) {
                  var key = localStorage.key(i);
                  items[key] = localStorage.getItem(key);
              }
              return items;
          """
            local_storage = driver.execute_script(local_storage_script)
            cookies_data['local_storage'] = local_storage
        except Exception as e:
            logging.debug(f"获取localStorage失败: {e}")

        # 保存到文件
        cookie_filename = f"{account}_cookies.json"
        cookie_filepath = os.path.join(COOKIES_DIR, cookie_filename)

        with open(cookie_filepath, 'w', encoding='utf-8') as f:
            json.dump(cookies_data, f, ensure_ascii=False, indent=2)

        logging.info(f"✅ Cookies已保存到: {cookie_filepath}")
        return cookie_filepath

    except Exception as e:
        logging.error(f"❌ 保存cookies失败: {e}")
        return None


# ============ 监控模块专用防重复机制 ============
def load_monitored_posts():
    """从文件中加载已监控分析的帖子ID"""
    monitored_posts = {
        "target_posts": [],
        "nontarget_posts": []
    }

    try:
        if os.path.exists(MONITORED_POSTS_FILE):
            try:
                with open(MONITORED_POSTS_FILE, 'r', encoding='utf-8') as f:
                    monitored_posts = json.load(f)
                print(
                    f"[监控模块] 已加载 {len(monitored_posts['target_posts'])} 个符合条件的帖子和 {len(monitored_posts['nontarget_posts'])} 个不符合条件的帖子")
            except json.JSONDecodeError:
                print("[监控模块] 已监控帖子文件格式错误，将创建新文件")
                save_monitored_posts(monitored_posts)
        else:
            print("[监控模块] 未找到已监控帖子文件，将创建新文件")
            save_monitored_posts(monitored_posts)
    except Exception as e:
        print(f"[监控模块] 加载已监控帖子时出错: {e}")
        save_monitored_posts(monitored_posts)

    return monitored_posts


def save_monitored_posts(monitored_posts):
    """保存已监控分析的帖子ID到文件"""
    try:
        os.makedirs(os.path.dirname(MONITORED_POSTS_FILE), exist_ok=True)

        with open(MONITORED_POSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(monitored_posts, f, ensure_ascii=False, indent=2)
        print(
            f"[监控模块] 已保存 {len(monitored_posts['target_posts'])} 个符合条件的帖子和 {len(monitored_posts['nontarget_posts'])} 个不符合条件的帖子")
    except Exception as e:
        print(f"[监控模块] 保存已监控帖子时出错: {e}")


def add_monitored_post(post_id, is_target):
    """添加新监控分析的帖子ID到持久化存储"""
    try:
        monitored_posts = load_monitored_posts()

        if is_target:
            if post_id not in monitored_posts["target_posts"]:
                monitored_posts["target_posts"].append(post_id)
                print(f"[监控模块] 添加帖子ID {post_id} 到符合条件列表")
        else:
            if post_id not in monitored_posts["nontarget_posts"]:
                monitored_posts["nontarget_posts"].append(post_id)
                print(f"[监控模块] 添加帖子ID {post_id} 到不符合条件列表")

        save_monitored_posts(monitored_posts)
    except Exception as e:
        print(f"[监控模块] 添加新监控帖子时出错: {e}")


def is_post_monitored(post_id):
    """检查帖子ID是否已经被监控分析过"""
    try:
        monitored_posts = load_monitored_posts()

        if post_id in monitored_posts["target_posts"]:
            return True, True
        elif post_id in monitored_posts["nontarget_posts"]:
            return True, False
        else:
            return False, None
    except Exception as e:
        print(f"[监控模块] 检查帖子监控状态时出错: {e}")
        return False, None


# ============ 发送模块专用防重复机制 ============
def load_processed_tasks():
    """从文件中加载已处理的任务ID"""
    processed_tasks = []

    try:
        if os.path.exists(PROCESSED_TASKS_FILE):
            try:
                with open(PROCESSED_TASKS_FILE, 'r', encoding='utf-8') as f:
                    processed_tasks = json.load(f)
                print(f"[发送模块] 已加载 {len(processed_tasks)} 个已处理的任务")
            except json.JSONDecodeError:
                print("[发送模块] 已处理任务文件格式错误，将创建新文件")
                save_processed_tasks(processed_tasks)
        else:
            print("[发送模块] 未找到已处理任务文件，将创建新文件")
            save_processed_tasks(processed_tasks)
    except Exception as e:
        print(f"[发送模块] 加载已处理任务时出错: {e}")
        save_processed_tasks(processed_tasks)

    return processed_tasks


def save_processed_tasks(processed_tasks):
    """保存已处理的任务ID到文件"""
    try:
        os.makedirs(os.path.dirname(PROCESSED_TASKS_FILE), exist_ok=True)

        with open(PROCESSED_TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed_tasks, f, ensure_ascii=False, indent=2)
        print(f"[发送模块] 已保存 {len(processed_tasks)} 个已处理的任务")
    except Exception as e:
        print(f"[发送模块] 保存已处理任务时出错: {e}")


def add_processed_task(post_id):
    """添加新处理的任务ID到持久化存储"""
    try:
        processed_tasks = load_processed_tasks()

        if post_id not in processed_tasks:
            processed_tasks.append(post_id)
            print(f"[发送模块] 添加任务ID {post_id} 到已处理列表")

        save_processed_tasks(processed_tasks)
    except Exception as e:
        print(f"[发送模块] 添加新处理任务时出错: {e}")


def is_task_processed(post_id):
    """检查任务ID是否已经处理过"""
    try:
        processed_tasks = load_processed_tasks()
        return post_id in processed_tasks
    except Exception as e:
        print(f"[发送模块] 检查任务处理状态时出错: {e}")
        return False


# ============ 通用函数 ============
def check_facebook_login_status(driver):
    """检查Facebook登录状态"""
    try:
        print("🔍 检查Facebook登录状态...")

        current_url = driver.current_url
        print(f"当前页面URL: {current_url}")

        if "login" in current_url.lower():
            print("❌ 当前在登录页面，cookie已失效")
            return False

        page_title = driver.title
        print(f"页面标题: {page_title}")

        if any(keyword in page_title.lower() for keyword in ['log in', 'sign in', 'login']):
            print("❌ 页面标题显示未登录状态")
            return False

        try:
            login_elements = driver.find_elements(By.XPATH, "//input[@type='password']")
            if login_elements:
                print("❌ 发现密码输入框，可能在登录页面")
                return False
        except:
            pass

        try:
            user_elements = driver.find_elements(By.XPATH, "//*[@role='banner']//img[@alt]")
            if user_elements:
                print("✅ 发现用户头像元素，可能已登录")
                return True

            nav_elements = driver.find_elements(By.XPATH, "//div[@role='navigation']")
            if nav_elements:
                print("✅ 发现导航栏元素，可能已登录")
                return True

        except:
            pass

        try:
            page_source = driver.page_source

            login_indicators = [
                "Log into Facebook",
                "Sign up for Facebook",
                "Email or phone number",
                "Password",
                "Forgotten password?"
            ]

            for indicator in login_indicators:
                if indicator in page_source:
                    print(f"❌ 发现登录指示器: {indicator}")
                    return False

            logged_in_indicators = [
                "What's on your mind",
                "Home",
                "Groups",
                "Marketplace"
            ]

            for indicator in logged_in_indicators:
                if indicator in page_source:
                    print(f"✅ 发现已登录指示器: {indicator}")
                    return True

        except:
            pass

        print("⚠️ 无法明确判断登录状态，假设已登录")
        return True

    except Exception as e:
        print(f"❌ 检查登录状态时出错: {e}")
        return False


def get_zhipu_keys():
    """获取ZhipuAI API密钥"""
    try:
        url = 'http://47.95.157.46:8520/api/zhipuai_key'

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(url, proxies=proxies, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get("success") and "data" in result:
                keys = [item["key"] for item in result["data"]]
                logger.info(f"获取到 {len(keys)} 个API密钥")
                return keys
    except Exception as e:
        logger.error(f"获取密钥失败: {e}")
    return []


def call_zhipu_api(api_key: str, prompt: str, max_retries: int = 1) -> Optional[str]:
    """调用ZhipuAI API"""
    import urllib3

    for attempt in range(max_retries):
        try:
            logger.info(f"ZhipuAI调用尝试 {attempt + 1}/{max_retries}")

            # 设置环境变量禁用代理
            os.environ['NO_PROXY'] = '*'
            os.environ['HTTP_PROXY'] = ''
            os.environ['HTTPS_PROXY'] = ''

            # 禁用SSL警告
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            from zhipuai import ZhipuAI

            # 创建客户端
            client = ZhipuAI(
                api_key=api_key,
                timeout=60,
                max_retries=2
            )

            response = client.chat.completions.create(
                model="glm-z1-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=4096,
                top_p=0.95
            )

            if response.choices and len(response.choices) > 0:
                content = response.choices[0].message.content
                if content:
                    # 清理可能的思考标签
                    cleaned_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                    logger.info(f"ZhipuAI调用成功 (尝试 {attempt + 1})")
                    return cleaned_content
                else:
                    logger.warning(f"ZhipuAI返回空内容 (尝试 {attempt + 1})")
            else:
                logger.warning(f"ZhipuAI响应格式异常 (尝试 {attempt + 1})")

        except ImportError as e:
            logger.error(f"ZhipuAI库导入失败: {e}")
            break
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"ZhipuAI调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")

            # 检查错误类型
            if any(keyword in error_msg for keyword in ['connection', 'timeout', 'network', 'ssl']):
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.info(f"网络错误，等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
            elif any(keyword in error_msg for keyword in ['api key', 'auth', 'unauthorized']):
                logger.error("API密钥错误，立即返回")
                return None
            elif any(keyword in error_msg for keyword in ['rate limit', 'quota', 'concurrent', '并发']):
                logger.error("API配额或并发限制，立即返回")
                return None

    logger.error("ZhipuAI调用失败")
    return None


def analyze_with_ai(prompt: str) -> str:
    """AI分析函数 - 只使用ZhipuAI，失败立即切换key"""
    logger.info("开始AI分析...")

    # 获取ZhipuAI密钥
    keys = get_zhipu_keys()
    if keys:
        logger.info(f"尝试使用ZhipuAI，共有 {len(keys)} 个密钥")
        for i, key in enumerate(keys):
            logger.info(f"尝试ZhipuAI第{i + 1}个密钥...")
            result = call_zhipu_api(key, prompt, max_retries=1)  # 每个key只尝试一次
            if result:
                logger.info("ZhipuAI调用成功")
                return result
            # key失败立即切换下一个，不等待
    else:
        logger.warning("未获取到ZhipuAI密钥")

    # 所有密钥都失败，返回默认分析
    logger.error("所有API密钥均不可用，返回默认结果")
    return """判定结果：否

判定依据：
AI服务暂时不可用，无法进行准确分析。为了避免误判，默认判定为非目标客户。

建议：
请稍后重试，或手动检查帖子内容是否包含以下特征：
1. 明确表达需要从中国采购商品
2. 提到发货到欧洲、美国等海外市场
3. 寻找供应商、代发服务或代理商
4. 询问物流、价格或合作相关问题"""


def parse_analysis_result(response: str) -> bool:
    """解析AI分析结果，判断是否为目标客户"""
    if not response:
        print(f"[解析分析结果] 响应为空，判定为非目标客户")
        return False

    print(f"[解析分析结果] 开始解析AI响应...")
    print(f"[解析分析结果] 响应内容预览: {response[:200]}...")

    response_lower = response.lower()

    # 主要判断逻辑：查找"判定结果：是"
    if "判定结果：是" in response:
        print(f"[解析分析结果] ✅ 找到'判定结果：是'，判定为目标客户")
        return True

    # 查找"判定结果：否"
    if "判定结果：否" in response:
        print(f"[解析分析结果] ❌ 找到'判定结果：否'，判定为非目标客户")
        return False

    # 兼容性检查：查找其他可能的肯定表达
    positive_patterns = [
        "是：该用户是中国代发潜在客户",
        "是：该用户是潜在代发客户",
        "该用户是中国代发潜在客户",
        "判定结果: 是",
        "判定结果 是",
        "结果：是",
        "结果: 是"
    ]

    for pattern in positive_patterns:
        if pattern in response:
            print(f"[解析分析结果] ✅ 找到肯定模式'{pattern}'，判定为目标客户")
            return True

    # 兼容性检查：查找其他可能的否定表达
    negative_patterns = [
        "否：该用户不是中国代发潜在客户",
        "否：该用户不是潜在代发客户",
        "该用户不是中国代发潜在客户",
        "判定结果: 否",
        "判定结果 否",
        "结果：否",
        "结果: 否"
    ]

    for pattern in negative_patterns:
        if pattern in response:
            print(f"[解析分析结果] ❌ 找到否定模式'{pattern}'，判定为非目标客户")
            return False

    # 默认情况下判定为非目标客户，避免误判
    print(f"[解析分析结果] ⚠️ 无法明确判定，默认判定为非目标客户")
    return False


def click_expand_button_and_get_full_content(post_element, driver):
    """点击帖子的展开按钮并获取展开后的完整内容 - 增强版本"""
    try:
        print(f"📝 开始获取帖子完整内容...")

        # 首先获取展开前的内容，用于对比
        original_content = post_element.text
        print(f"📝 原始内容长度: {len(original_content)}")
        print(f"📝 原始内容预览: {original_content[:200]}...")

        # 检查是否有展开标识
        expand_indicators = ["… 展开", "... See more", "... Show more", "展开更多", "查看更多"]
        has_expand_indicator = any(indicator in original_content for indicator in expand_indicators)

        if not has_expand_indicator:
            print(f"🔍 未发现展开标识，可能已是完整内容")
            return original_content

        print(f"🔍 发现展开标识，尝试查找展开按钮...")

        # 查找展开按钮的多种选择器
        expand_selectors = [
            # 通过文本匹配
            ".//div[contains(text(), '展开')]",
            ".//div[contains(text(), 'See more')]",
            ".//div[contains(text(), 'Show more')]",
            ".//span[contains(text(), '展开')]",
            ".//span[contains(text(), 'See more')]",
            ".//span[contains(text(), 'Show more')]",
            ".//button[contains(text(), '展开')]",
            ".//button[contains(text(), 'See more')]",
            ".//button[contains(text(), 'Show more')]",
            # 通过role匹配
            ".//div[@role='button' and contains(text(), '展开')]",
            ".//div[@role='button' and contains(text(), 'See more')]",
            ".//div[@role='button' and contains(text(), 'Show more')]",
            # 更宽泛的匹配
            ".//*[contains(text(), '展开')]",
            ".//*[contains(text(), 'See more')]",
            ".//*[contains(text(), 'Show more')]"
        ]

        expand_button_found = None

        for selector in expand_selectors:
            try:
                expand_elements = post_element.find_elements(By.XPATH, selector)
                for element in expand_elements:
                    try:
                        if not element.is_displayed() or not element.is_enabled():
                            continue

                        button_text = element.text.strip()
                        if any(keyword in button_text for keyword in ['展开', 'See more', 'Show more']):
                            expand_button_found = element
                            print(f"✅ 找到展开按钮: '{button_text}'")
                            break
                    except Exception as e:
                        continue

                if expand_button_found:
                    break

            except Exception as e:
                continue

        if not expand_button_found:
            print(f"❌ 未找到展开按钮，返回原始内容")
            return original_content

        # 尝试点击展开按钮
        print(f"🎯 尝试点击展开按钮...")

        try:
            # 滚动到按钮位置
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                                  expand_button_found)
            time.sleep(1)

            # 尝试多种点击方式
            click_success = False
            try:
                expand_button_found.click()
                click_success = True
                print(f"✅ 常规点击成功")
            except Exception as e1:
                try:
                    driver.execute_script("arguments[0].click();", expand_button_found)
                    click_success = True
                    print(f"✅ JavaScript点击成功")
                except Exception as e2:
                    try:
                        actions = ActionChains(driver)
                        actions.move_to_element(expand_button_found).click().perform()
                        click_success = True
                        print(f"✅ ActionChains点击成功")
                    except Exception as e3:
                        print(f"❌ 所有点击方法都失败了")

            if not click_success:
                return original_content

            # 等待内容加载
            print(f"⏳ 等待内容展开...")
            max_wait_time = 8
            check_interval = 0.5
            waited_time = 0

            while waited_time < max_wait_time:
                try:
                    new_content = post_element.text
                    new_length = len(new_content)

                    # 检查内容是否增加
                    if new_length > len(original_content):
                        print(f"✅ 内容已展开! 原长度: {len(original_content)} -> 新长度: {new_length}")
                        # 检查是否还有展开标识
                        remaining_indicators = any(indicator in new_content for indicator in expand_indicators)
                        if not remaining_indicators:
                            print(f"🎉 内容完全展开，无剩余展开标识")
                            return new_content
                        else:
                            print(f"🔄 内容部分展开，但仍有展开标识")
                            return new_content

                except Exception as e:
                    print(f"⚠️ 检查内容时出错: {e}")

                time.sleep(check_interval)
                waited_time += check_interval

            # 等待超时，获取当前内容
            try:
                final_content = post_element.text
                print(f"📊 最终内容长度: {len(final_content)}")
                return final_content

            except Exception as e:
                print(f"❌ 获取最终内容时出错: {e}")
                return original_content

        except Exception as e:
            print(f"❌ 展开过程出错: {e}")
            return original_content

    except Exception as e:
        print(f"❌ 获取帖子完整内容时出错: {e}")
        try:
            return post_element.text
        except:
            return ""


def make_proxy_request(post_content, post_id, team_name, timeout=30):
    """发送帖子数据到API - 修复版本，使用正确的参数名"""
    request_url = 'http://47.95.157.46:8520/api/new_data'

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    # 使用正确的参数名，根据API调用示例
    form_data = {
        "postId": post_id,  # 帖子ID
        "teamName": team_name,  # 团队名称
        "postContent": post_content  # 帖子内容
    }
    data = parse.urlencode(form_data, True)

    # 禁用代理
    proxies = {
        'http': None,
        'https': None
    }

    try:
        print(f"📡 发送API请求: postId={post_id}, teamName={team_name}, 内容长度={len(post_content)}")

        # 显示发送的内容预览，用于调试
        print(f"📄 发送内容预览: {post_content[:300]}...")

        response = requests.post(
            request_url,
            headers=headers,
            data=data,
            proxies=proxies,
            timeout=timeout
        )
        return {
            "status_code": response.status_code,
            "response": response.text
        }
    except requests.exceptions.RequestException as e:
        return {
            "status_code": None,
            "response": f"请求失败: {e}"
        }


def setup_independent_driver(cookies_file=None):
    """创建独立的浏览器实例"""
    print("创建独立浏览器实例...")

    try:
        chrome_options = Options()

        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-infobars")
        chrome_options.add_experimental_option("detach", True)

        chrome_options.add_argument("--window-name=Facebook-GroupMonitor")

        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        user_agent = random.choice(USER_AGENTS)
        chrome_options.add_argument(f"--user-agent={user_agent}")

        # 使用独立的用户数据目录
        user_data_dir = USER_DATA_DIRS["monitor"]
        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

        driver = webdriver.Chrome(options=chrome_options)
        print("浏览器实例创建成功")

        if cookies_file:
            print("加载cookies...")
            load_cookies(driver, cookies_file)

        return driver

    except Exception as e:
        print(f"创建浏览器实例失败: {e}")
        return None


def load_cookies(driver, cookies_file):
    """加载cookies到浏览器"""
    print(f"加载cookies文件: {cookies_file}")

    try:
        driver.get("https://www.facebook.com")
        time.sleep(2)

        with open(cookies_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict) and 'cookies' in data:
            cookies = data['cookies']
            local_storage = data.get('local_storage', {})
        elif isinstance(data, list):
            cookies = data
            local_storage = {}
        else:
            print("无法识别的cookies文件格式")
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
                print(f"添加cookie时出错: {e}")

        print("Cookies已加载")

        if local_storage:
            print("加载本地存储数据...")
            for key, value in local_storage.items():
                try:
                    driver.execute_script(f"window.localStorage.setItem('{key}', '{value}');")
                except Exception as e:
                    print(f"设置localStorage ({key})时出错: {e}")

        driver.refresh()
        time.sleep(3)

        if "Facebook" in driver.title:
            print("成功登录到Facebook")
            return True
        else:
            print("登录可能未完成，请检查浏览器是否已登录")
            return True
    except Exception as e:
        print(f"加载cookies时出错: {e}")
        return False


def download_cookies_from_url(cookie_url, filename=None):
    """从URL下载cookie文件"""
    print(f"🌐 开始从URL下载cookie文件: {cookie_url}")

    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)

        if not filename:
            filename = f"facebook_cookies_{int(time.time())}.json"

        if not filename.endswith('.json'):
            filename += '.json'

        cookie_file_path = os.path.join(COOKIES_DIR, filename)

        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        print("📥 正在下载...")
        response = requests.get(cookie_url, headers=headers, proxies=proxies, timeout=30)

        if response.status_code != 200:
            print(f"❌ 下载失败，HTTP状态码: {response.status_code}")
            return None

        try:
            content = response.text
            json_data = json.loads(content)
            print(f"✅ JSON格式验证通过")

            if isinstance(json_data, dict) and 'cookies' in json_data:
                print(f"📊 检测到cookies格式，包含 {len(json_data['cookies'])} 个cookie")
            elif isinstance(json_data, list):
                print(f"📊 检测到cookies数组格式，包含 {len(json_data)} 个cookie")
            else:
                print("⚠️ 未识别的cookie格式，但仍尝试使用")

        except json.JSONDecodeError as e:
            print(f"❌ 下载的文件不是有效的JSON格式: {e}")
            return None

        with open(cookie_file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        file_size = os.path.getsize(cookie_file_path)
        print(f"💾 文件已保存，大小: {file_size} 字节")

        if file_size == 0:
            print("❌ 下载的文件为空")
            os.remove(cookie_file_path)
            return None

        print(f"✅ Cookie文件下载成功: {cookie_file_path}")
        return cookie_file_path

    except Exception as e:
        print(f"❌ 下载cookie文件时出错: {e}")
        return None


class AsyncAnalysisManager:
    """异步AI分析管理器"""

    def __init__(self, team_name, max_workers=2):
        self.team_name = team_name  # 保存团队名称
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.analysis_queue = queue.Queue()
        self.results = {}
        self.running = True

        self.analysis_thread = threading.Thread(target=self._analysis_worker, daemon=True)
        self.analysis_thread.start()

    def _analysis_worker(self):
        """后台分析工作线程"""
        while self.running:
            try:
                if not self.analysis_queue.empty():
                    task = self.analysis_queue.get()
                    post_id = task['post_id']
                    post_content = task['post_content']

                    print(f"[异步分析] 开始分析帖子 {post_id}")

                    # 使用完整的提示词
                    prompt_template = """请对以下帖子做角色与需求识别，判断发帖人是否属于有"从中国采购并发货到国外"需求的**潜在代发客户**

输入内容：
文本内容：{用户原文}

分析要求：
1. **角色判定**  
 - **买家 (潜在客户)**：明确表达「想买 / 需要某商品」并希望从中国发往其所在国或目标市场。典型用语：  
   - looking for / need / want to buy … shipped from China (to …)  
   - can anyone source / dropship / supply … to my country …  
 - **卖家 / 供应商 / 服务商**：宣传自己能生产、定制、备货、仓储或提供国际物流。典型用语：  
   - customization available / we offer / I supply / in-stock / wholesale price / ready to ship  
   - international shipping available / dropshipping service provided / DM me for quotation

2. 语义理解：
 识别用户是否有从中国采购并发货到国外的需求
 判断用户是否在寻找代发服务、代理或供应商
 提取目标市场(如欧洲、意大利等)和期望的物流时效等信息

3. 关键词识别：
 检测文本中是否包含代发业务相关关键词或语义表达

4. 需求判断：
 分析用户是否需要中国代发/代sourcing服务
 给出明确的判断理由

目标客户特征：

关键表达（满足任一即可）：
1. 代发/代购需求表达：
 looking for products to ship to [国家/地区]
 need items shipped from China to [国家/地区]
 looking for Chinese supplier/agent
 need shipping service from China
 sourcing products from China

2. 物流需求表达：
 shipping to Europe/Italy/[其他国家]
 ship to Europe/Italy/[其他国家]
 deliver to my country
 shipping time to [国家/地区]
 fast delivery to [国家/地区]

3. 核心业务关键词：
 supplier, dropshipping, agent, shipping, China, products
 sourcing, purchasing, delivery, shipment
 order fulfillment, warehousing, inventory
 logistics, international shipping, customs clearance

识别特征：
用户表达了从中国购买/发送商品的意愿
提到特定产品类型及目的地市场
可能询问价格、物流时效或服务细节
通常使用英语或简单直接的表达方式

输出格式：

判定结果：
是/否：该用户是/不是中国代发潜在客户

判定依据：
1. 关键表达匹配：找出文本中的代发相关表达
2. 需求明确性：用户是否明确表达了代发需求
3. 市场匹配度：目标市场是否符合我们的服务范围(如欧洲)
4. 产品可行性：所提及产品是否适合代发业务

示例分析：
输入：I am looking for heated slippers to ship to Europe
判定结果：是
判定依据：
关键表达匹配：包含"ship to Europe"，明确表达物流需求
需求明确性：明确表达了需要产品(heated slippers)并发往欧洲
市场匹配度：目标市场为欧洲，符合代发业务范围
产品可行性：加热拖鞋属于常见消费品，适合代发业务"""

                    prompt = prompt_template.replace("{用户原文}", post_content)

                    try:
                        response = analyze_with_ai(prompt)
                        print(f"[异步分析] 帖子 {post_id} AI分析完成")

                        is_target = parse_analysis_result(response)

                        print(
                            f"[异步分析] 帖子 {post_id} 最终判定: {'✅ 是目标客户' if is_target else '❌ 不是目标客户'}")

                        add_monitored_post(post_id, is_target)

                        if is_target:
                            print(f"[异步分析] 🚀 满足目标客户条件，发送数据到API...")
                            api_response = make_proxy_request(
                                post_content=post_content,
                                post_id=post_id,
                                team_name=self.team_name,  # 使用保存的团队名称
                                timeout=30
                            )
                            print(f"[异步分析] 📡 API响应: {api_response}")
                        else:
                            print(f"[异步分析] ⏭️ 不符合条件，跳过API调用")

                        self.results[post_id] = {
                            'is_target': is_target,
                            'response': response,
                            'analyzed_at': time.time()
                        }

                    except Exception as e:
                        print(f"[异步分析] ❌ 分析帖子 {post_id} 时出错: {e}")
                        self.results[post_id] = {
                            'is_target': False,
                            'response': f"分析失败: {e}",
                            'analyzed_at': time.time()
                        }

                    self.analysis_queue.task_done()
                else:
                    time.sleep(1)

            except Exception as e:
                print(f"[异步分析] ❌ 分析工作线程出错: {e}")
                time.sleep(5)

    def submit_analysis(self, post_id, post_content):
        """提交分析任务"""
        is_analyzed, is_target = is_post_monitored(post_id)
        if is_analyzed:
            print(f"[异步分析] 帖子 {post_id} 已分析过，跳过")
            return

        task = {
            'post_id': post_id,
            'post_content': post_content
        }
        self.analysis_queue.put(task)
        print(f"[异步分析] 已提交帖子 {post_id} 到分析队列，当前队列长度: {self.analysis_queue.qsize()}")

    def stop(self):
        """停止分析管理器"""
        self.running = False


def monitor_facebook_groups(cookies_file, account, team_name):
    """持续监控Facebook群组活动"""
    print("=" * 60)
    print("启动Facebook群组帖子监控")
    print("=" * 60)

    if not cookies_file:
        print("未提供cookie文件路径")
        return False

    driver = setup_independent_driver(cookies_file)
    if not driver:
        print("群组监控浏览器创建失败")
        return False

    # 全局异步分析管理器，传入团队名称
    async_analyzer = AsyncAnalysisManager(team_name)

    try:
        print("开始持续监控Facebook群组活动...")
        already_processed_ids = set()
        round_count = 1
        login_check_interval = 5

        while True:
            try:
                print(f"\n===== 群组监控第{round_count}轮 =====")

                # 定期检查登录状态
                if round_count % login_check_interval == 1:
                    print("🔍 检查Facebook登录状态...")
                    if not check_facebook_login_status(driver):
                        print("❌ 检测到Facebook登录失效!")
                        print("📡 向系统报告cookie失效状态...")

                        report_success = report_cookie_status(account, "失效")
                        if report_success:
                            print("✅ 成功报告cookie失效状态")
                        else:
                            print("❌ 报告cookie失效状态失败")

                        print("🔄 开始自动重新获取cookie并重新登录...")
                        return "LOGIN_EXPIRED"
                    else:
                        print("✅ Facebook登录状态正常")

                # 访问Facebook群组页面
                driver.get("https://www.facebook.com/?filter=groups&sk=h_chr")
                time.sleep(5)
                print("已打开Facebook群组页面")

                round_processed = 0
                scroll_count = 0
                max_scrolls = 10

                while scroll_count < max_scrolls:
                    try:
                        post_elements = driver.find_elements(By.XPATH, "//div[@role='article']")
                        current_count = len(post_elements)
                        print(f"找到{current_count}个帖子元素")

                        if current_count == 0:
                            print("未找到帖子，尝试滚动页面...")
                            driver.execute_script("window.scrollTo(0, window.scrollY + 500);")
                            time.sleep(2)
                            scroll_count += 1
                            continue

                        new_posts_found = False
                        for post_index, post in enumerate(post_elements):
                            try:
                                print(f"\n📋 处理第 {post_index + 1}/{current_count} 个帖子")

                                # 获取帖子ID
                                post_html = post.get_attribute('outerHTML')
                                post_id_match = re.search(r'(?<=multi_permalinks=)[\s\S]*?(?=&amp;__cft)', post_html)
                                if not post_id_match:
                                    print("   ❌ 未找到帖子ID，跳过")
                                    continue

                                post_id = post_id_match.group(0)
                                if "&amp" in post_id:
                                    id_match = re.search(r'^(\d+)(?=&amp;)', post_id)
                                    if id_match:
                                        post_id = id_match.group(0)
                                    else:
                                        print("   ❌ 帖子ID格式错误，跳过")
                                        continue

                                if post_id in already_processed_ids:
                                    print(f"   🔄 帖子 {post_id} 已处理过，跳过")
                                    continue

                                print(f"   🆔 帖子ID: {post_id}")

                                # 点击展开按钮并获取完整内容
                                print(f"   📖 开始获取帖子完整内容...")
                                post_content = click_expand_button_and_get_full_content(post, driver)

                                if not post_content:
                                    print("   ❌ 未获取到帖子内容，跳过")
                                    continue

                                new_posts_found = True
                                print(f"   ✅ 成功获取帖子内容 (长度: {len(post_content)})")
                                print(f"   📄 内容预览: {post_content[:100]}...")

                                # 时间检查
                                time_match = re.search(r'(?<=·)[\s\S]*?(?=分钟)', post_content)
                                if time_match:
                                    try:
                                        post_time = time_match.group(0).strip()
                                        post_minutes = int(post_time)
                                        print(f"   ⏰ 帖子时间: {post_minutes}分钟前")
                                        if post_minutes >= 40:
                                            print(f"   ⏰ 帖子过旧，停止当前轮次")
                                            scroll_count = max_scrolls
                                    except ValueError:
                                        pass

                                # 检查是否已分析过
                                is_analyzed, is_target = is_post_monitored(post_id)
                                if is_analyzed:
                                    print(f"   📊 帖子 {post_id} 已分析过: {'符合条件' if is_target else '不符合条件'}")
                                    already_processed_ids.add(post_id)
                                    continue

                                # 提交到异步分析队列
                                print(f"   🚀 提交到异步分析队列...")
                                async_analyzer.submit_analysis(post_id, post_content)
                                already_processed_ids.add(post_id)
                                round_processed += 1

                            except Exception as e:
                                print(f"   ❌ 处理帖子时出错: {e}")
                                continue

                        if not new_posts_found or scroll_count >= max_scrolls:
                            break

                        print("📜 向下滚动加载更多帖子...")
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        scroll_count += 1
                        time.sleep(3)

                    except Exception as e:
                        print(f"处理帖子元素时出错: {e}")
                        break

                print(f"\n✅ 群组监控第{round_count}轮完成，处理了{round_processed}个新帖子")
                print(f"📊 当前分析队列长度: {async_analyzer.analysis_queue.qsize()}")

                # 缩短等待时间到10秒
                wait_time = 10
                print(f"⏰ 等待{wait_time}秒后开始下一轮...")
                time.sleep(wait_time)
                round_count += 1

            except Exception as e:
                print(f"群组监控循环中出错: {e}")
                print("等待10秒后尝试恢复...")
                time.sleep(10)
                round_count += 1

    except KeyboardInterrupt:
        print("\n群组监控被用户中断")
        return False
    except Exception as e:
        print(f"群组监控出错: {e}")
        return False
    finally:
        print("关闭群组监控浏览器...")
        if driver:
            try:
                driver.quit()
            except:
                pass
        async_analyzer.stop()


def start_facebook_group_monitor(account, team_name):
    """启动Facebook群组监控的主函数"""
    print("=" * 60)
    print("Facebook群组帖子监控工具")
    print("=" * 60)
    print(f"账户: {account}")
    print(f"团队: {team_name}")
    print("此程序将自动获取cookie文件，监控Facebook群组帖子")
    print("=" * 60)

    retry_count = 0
    max_retries = 999

    while retry_count < max_retries:
        try:
            retry_count += 1

            if retry_count > 1:
                print(f"\n🔄 第{retry_count}次尝试启动监控...")

            # 从API获取cookie URL
            print(f"\n🌐 步骤1: 从API获取cookie URL...")
            cookie_url = get_cookie_url_from_api(account)
            if not cookie_url:
                print("❌ 获取cookie URL失败")
                if retry_count >= max_retries:
                    print("❌ 达到最大重试次数，程序退出")
                    return False

                print("⏰ 等待5分钟后重试...")
                time.sleep(300)
                continue

            # 下载cookie文件
            print(f"\n📥 步骤2: 下载cookie文件...")
            cookies_file = download_cookies_from_url(cookie_url, f"{account}_cookies.json")
            if not cookies_file:
                print("❌ Cookie文件下载失败")
                if retry_count >= max_retries:
                    print("❌ 达到最大重试次数，程序退出")
                    return False

                print("⏰ 等待5分钟后重试...")
                time.sleep(300)
                continue

            print("✅ Cookie文件验证通过")

            # 加载数据
            print(f"\n📊 步骤3: 加载已分析的帖子数据...")
            load_monitored_posts()

            # 启动群组监控
            print(f"\n🚀 步骤4: 启动Facebook群组帖子监控...")
            print(f"📁 使用Cookie文件: {cookies_file}")

            monitor_result = monitor_facebook_groups(cookies_file, account, team_name)

            # 检查监控结果
            if monitor_result == "LOGIN_EXPIRED":
                print("\n⚠️ 检测到登录失效，将在5分钟后自动重新获取cookie并重启监控...")
                print("⏰ 等待5分钟...")
                time.sleep(300)
                continue
            elif monitor_result == False:
                print("\n❌ 监控过程中出现错误")
                if retry_count >= max_retries:
                    print("❌ 达到最大重试次数，程序退出")
                    return False

                print("⏰ 等待5分钟后重试...")
                time.sleep(300)
                continue
            else:
                print("\n✅ 监控正常结束")
                return True

        except KeyboardInterrupt:
            print("\n⚠️ 检测到键盘中断，程序退出...")
            return False
        except Exception as e:
            print(f"❌ 第{retry_count}次尝试失败: {e}")
            if retry_count >= max_retries:
                print("❌ 达到最大重试次数，程序退出")
                return False

            print("⏰ 等待5分钟后重试...")
            time.sleep(300)
            continue

    print("❌ 程序异常退出")
    return False


# ============ 发送模块 ============
def load_messaged_users():
    """从文件中加载已收到私信的用户ID记录"""
    messaged_users = {}

    try:
        if os.path.exists(MESSAGED_USERS_FILE):
            try:
                with open(MESSAGED_USERS_FILE, 'r', encoding='utf-8') as f:
                    messaged_users = json.load(f)
                print(f"[发送模块] 已加载 {len(messaged_users)} 个账号的已私信用户记录")
            except json.JSONDecodeError:
                print("[发送模块] 已私信用户记录文件格式错误，将创建新文件")
                save_messaged_users(messaged_users)
        else:
            print("[发送模块] 未找到已私信用户记录文件，将创建新文件")
            save_messaged_users(messaged_users)
    except Exception as e:
        print(f"[发送模块] 加载已私信用户记录时出错: {e}")
        save_messaged_users(messaged_users)

    return messaged_users


def save_messaged_users(messaged_users):
    """保存已收到私信的用户ID记录到文件"""
    try:
        with open(MESSAGED_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(messaged_users, f, ensure_ascii=False, indent=2)
        print(f"[发送模块] 已保存 {len(messaged_users)} 个账号的已私信用户记录")
    except Exception as e:
        print(f"[发送模块] 保存已私信用户记录时出错: {e}")


def is_user_messaged(account_number, user_id):
    """检查用户是否已经收到指定账号的私信"""
    try:
        messaged_users = load_messaged_users()

        if account_number not in messaged_users:
            return False

        return user_id in messaged_users[account_number]
    except Exception as e:
        print(f"[发送模块] 检查用户私信状态时出错: {e}")
        return False


def add_messaged_user(account_number, user_id):
    """添加用户到指定账号的已私信用户记录"""
    try:
        messaged_users = load_messaged_users()

        if account_number not in messaged_users:
            messaged_users[account_number] = []

        if user_id not in messaged_users[account_number]:
            messaged_users[account_number].append(user_id)
            print(f"[发送模块] 已将用户ID {user_id} 添加到账号 {account_number} 的已私信用户列表")

        save_messaged_users(messaged_users)
    except Exception as e:
        print(f"[发送模块] 添加已私信用户时出错: {e}")


def get_facebook_task(team_name):
    """获取Facebook任务"""
    try:
        print(f"正在获取Facebook任务，teamName参数: {team_name}")

        request_url = 'http://47.95.157.46:8520/api/get_Facebook'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        form_data = {"teamName": team_name}
        data = parse.urlencode(form_data, True)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=10)

        print(f"API响应状态码: {response.status_code}")
        print(f"API响应内容: {response.text}")

        if response.status_code == 200:
            result = response.json()

            if result.get("success") and result.get("data"):
                data_array = result["data"]
                if len(data_array) >= 2 and data_array[1] == 1 and len(data_array[0]) > 0:
                    task_info = data_array[0][0]
                    post_content = task_info.get('post_content', '')
                    post_id = task_info.get('post_id', '')

                    if post_content and post_id:
                        print("\n===== 开始处理帖子内容 =====")
                        # 使用【·】分割，删除第一个元素，然后合并剩余内容
                        parts = post_content.split('·')
                        if len(parts) > 1:
                            parts.pop(0)
                            processed_post_content = '·'.join(parts).strip()
                        else:
                            processed_post_content = post_content
                        print("===== 帖子内容处理完成 =====\n")

                        post_url = f"https://www.facebook.com/{post_id}"

                        print(f"成功获取Facebook任务:")
                        print(f"帖子ID: {post_id}")
                        print(f"帖子URL: {post_url}")
                        print(f"处理后的帖子内容预览: {processed_post_content[:100]}...")

                        return {
                            'post_content': processed_post_content,
                            'post_id': post_id,
                            'post_url': post_url
                        }

        print("API返回失败或无任务")
        return None

    except Exception as e:
        print(f"获取Facebook任务时出错: {str(e)}")
        return None


def get_comment_and_message_content(team_name):
    """从API获取评论和私信内容 - 修复版本"""
    comment_text = None
    message_text = None

    # 禁用代理
    proxies = {
        'http': None,
        'https': None
    }

    try:
        # 获取评论内容
        print(f"正在从API获取评论内容，teamName: {team_name}")
        request_url = 'http://47.95.157.46:8520/api/api/announcement/one'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        comment_form_data = {"teamName": team_name, "scriptType": "评论"}
        comment_data = parse.urlencode(comment_form_data, True)

        comment_response = requests.post(request_url, headers=headers, data=comment_data, proxies=proxies, timeout=10)

        if comment_response.status_code == 200:
            comment_result = comment_response.json()
            if comment_result.get("success") and comment_result.get("data"):
                # 修复：处理API返回的数组格式
                data = comment_result.get("data")
                if isinstance(data, list) and len(data) > 0:
                    # 取第一个元素的script_text字段
                    first_item = data[0]
                    if isinstance(first_item, dict) and "script_text" in first_item:
                        comment_text = first_item["script_text"].strip()
                        if comment_text:
                            print(f"获取到评论内容预览: {comment_text[:30]}...")
                    else:
                        print("❌ 评论API返回数据格式不正确")
                else:
                    print("❌ 评论API返回数据为空或格式错误")
            else:
                print(f"❌ 评论API返回失败: {comment_result.get('msg', '未知错误')}")
        else:
            print(f"❌ 评论API请求失败，状态码: {comment_response.status_code}")

        # 获取私信内容
        print(f"正在从API获取私信内容，teamName: {team_name}")
        message_form_data = {"teamName": team_name, "scriptType": "私信"}
        message_data = parse.urlencode(message_form_data, True)

        message_response = requests.post(request_url, headers=headers, data=message_data, proxies=proxies, timeout=10)

        if message_response.status_code == 200:
            message_result = message_response.json()
            if message_result.get("success") and message_result.get("data"):
                # 修复：处理API返回的数组格式
                data = message_result.get("data")
                if isinstance(data, list) and len(data) > 0:
                    # 取第一个元素的script_text字段
                    first_item = data[0]
                    if isinstance(first_item, dict) and "script_text" in first_item:
                        message_text = first_item["script_text"].strip()
                        if message_text:
                            # 移除Whatsapp:前缀（如果存在）
                            if message_text.startswith("Whatsapp:"):
                                message_text = message_text.replace("Whatsapp:", "", 1).strip()
                            print(f"获取到私信内容预览: {message_text[:30]}...")
                    else:
                        print("❌ 私信API返回数据格式不正确")
                else:
                    print("❌ 私信API返回数据为空或格式错误")
            else:
                print(f"❌ 私信API返回失败: {message_result.get('msg', '未知错误')}")
        else:
            print(f"❌ 私信API请求失败，状态码: {message_response.status_code}")

    except Exception as e:
        print(f"从API获取评论和私信内容时出错: {e}")

    return comment_text, message_text


def extract_user_link(driver):
    """提取所有超链接并找出帖子作者的个人资料链接"""
    try:
        print("正在获取页面所有超链接...")
        page_html = driver.page_source

        all_links = driver.find_elements(By.TAG_NAME, "a")
        print(f"找到 {len(all_links)} 个超链接")

        href_list = []
        user_ids = []

        for index, link in enumerate(all_links):
            try:
                href = link.get_attribute("href")
                if href:
                    if href.endswith("]-R"):
                        continue

                    href_list.append(href)

                    id_match = re.search(r'(?<=user/)[\s\S]*?(?=/\?__cft__\[0])', href)
                    if id_match:
                        user_id = id_match.group(0)
                        if user_id:
                            user_ids.append(user_id)
                            print(f"   ✅ 提取到用户ID: {user_id}")
            except Exception as e:
                continue

        print(f"共提取到 {len(user_ids)} 个用户ID")

        if user_ids:
            unique_user_ids = []
            for uid in user_ids:
                if uid not in unique_user_ids:
                    unique_user_ids.append(uid)

            if len(unique_user_ids) > 1:
                removed_id = unique_user_ids.pop(0)
                print(f"删除了第一个ID: {removed_id}")

            if unique_user_ids:
                publisher_id = unique_user_ids[0]
                user_link = f"https://www.facebook.com/profile.php?id={publisher_id}"
                print(f"构建用户个人资料链接: {user_link}")
                return user_link, publisher_id

        # 尝试其他方法
        for href in href_list:
            if href.endswith("]-R"):
                continue

            simple_match = re.search(r'/user/(\d+)', href)
            if simple_match:
                user_id = simple_match.group(1)
                if user_id:
                    user_link = f"https://www.facebook.com/profile.php?id={user_id}"
                    print(f"✅ 使用备用正则表达式找到用户ID: {user_id}")
                    return user_link, user_id

            profile_match = re.search(r'profile\.php\?id=(\d+)', href)
            if profile_match:
                user_id = profile_match.group(1)
                if user_id:
                    user_link = f"https://www.facebook.com/profile.php?id={user_id}"
                    print(f"✅ 找到个人资料链接的用户ID: {user_id}")
                    return user_link, user_id

        print("❌ 错误：无法提取作者ID")
        return None, None

    except Exception as e:
        print(f"提取作者个人资料链接时出错: {e}")
        return None, None


def get_customer_nickname(driver):
    """获取客户昵称"""
    try:
        nickname_element = driver.find_element(By.XPATH, "//h1[contains(@class, 'html-h1')]")
        if nickname_element:
            nickname_text = nickname_element.text.strip()
            nickname = nickname_text.split('\n')[0].strip() if '\n' in nickname_text else nickname_text
            print(f"获取到客户昵称: {nickname}")
            return nickname
    except Exception as e:
        print(f"获取客户昵称时出错: {e}")
        try:
            selectors = [
                "//h1",
                "//span[contains(@dir, 'auto')]",
                "//*[contains(@class, 'profileName')]"
            ]
            for selector in selectors:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed() and element.text.strip():
                        nickname = element.text.strip().split('\n')[0]
                        if len(nickname) > 0 and len(nickname) < 100:
                            print(f"使用备用方法获取到客户昵称: {nickname}")
                            return nickname
        except Exception as e2:
            print(f"备用方法获取客户昵称也失败: {e2}")

    return "未知客户"


def comment_on_post(driver, post_url, comment_text, account_number, team_name):
    """在指定帖子下发表评论 - 增强版本，添加限制检测"""
    print(f"正在访问帖子: {post_url}")
    try:
        driver.get(post_url)
        time.sleep(5)

        # 检测发送限制
        is_restricted, restriction_text = detect_sending_restriction(driver)
        if is_restricted:
            print(f"🚫 检测到评论限制: {restriction_text}")
            # 更新账号状态为封禁
            update_account_status(account_number, "封禁")
            # 释放帖子ID
            post_id = post_url.split('/')[-1]
            release_post_id(post_id, team_name)
            return False, None, None, f"评论被限制: {restriction_text}"

        try:
            deny_buttons = driver.find_elements(By.XPATH,
                                                "//button[contains(text(), '禁止') or contains(text(), 'Block')]")
            for button in deny_buttons:
                if button.is_displayed():
                    print("发现通知弹窗，点击禁止")
                    button.click()
                    time.sleep(1)
                    break
        except:
            pass

        driver.execute_script("window.scrollBy(0, 300);")
        time.sleep(2)

        print("寻找评论区...")

        try:
            comment_buttons = driver.find_elements(By.XPATH, "//div[@aria-label='评论']")
            if comment_buttons:
                for button in comment_buttons:
                    if button.is_displayed():
                        print("找到评论按钮，点击激活评论区...")
                        driver.execute_script("arguments[0].click();", button)
                        time.sleep(2)
                        break
        except Exception as e:
            print(f"点击评论按钮时出错: {e}")

        comment_box = None
        user_xpath = "//div[(@aria-label='发表公开评论…' or @aria-label='输入回答…' or @aria-label='提交首条评论…'or @aria-label='写评论…') and @contenteditable='true' and @role='textbox']"

        try:
            elements = driver.find_elements(By.XPATH, user_xpath)
            for element in elements:
                if element.is_displayed():
                    comment_box = element
                    print("找到评论框(用户XPath)")
                    break
        except Exception as e:
            print(f"使用用户提供的XPath查找评论框时出错: {e}")

        if not comment_box:
            backup_selectors = [
                "//div[@contenteditable='true' and @role='textbox']",
                "//div[contains(@aria-label, '评论') and @contenteditable='true']",
                "//form//div[@role='textbox']",
                "//div[@data-lexical-editor='true']"
            ]

            for selector in backup_selectors:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        if element.is_displayed():
                            comment_box = element
                            print(f"找到评论框(备用选择器): {selector}")
                            break
                    if comment_box:
                        break
                except:
                    continue

        if comment_box:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment_box)
            time.sleep(1)

            try:
                driver.execute_script("arguments[0].click();", comment_box)
                time.sleep(1)

                actions = ActionChains(driver)
                actions.move_to_element(comment_box).click().perform()
                time.sleep(1)

                driver.execute_script("arguments[0].innerHTML = '';", comment_box)

                # 逐字符输入
                for char in comment_text:
                    comment_box.send_keys(char)
                    time.sleep(random.uniform(0.05, 0.15))

                time.sleep(2)

                # 再次检测发送限制
                is_restricted, restriction_text = detect_sending_restriction(driver)
                if is_restricted:
                    print(f"🚫 输入后检测到评论限制: {restriction_text}")
                    update_account_status(account_number, "封禁")
                    post_id = post_url.split('/')[-1]
                    release_post_id(post_id, team_name)
                    return False, None, None, f"评论被限制: {restriction_text}"

                comment_box.send_keys(Keys.ENTER)

                print("评论发布中，等待5秒...")
                time.sleep(5)

                # 发送后检测限制
                is_restricted, restriction_text = detect_sending_restriction(driver)
                if is_restricted:
                    print(f"🚫 发送后检测到评论限制: {restriction_text}")
                    update_account_status(account_number, "封禁")
                    post_id = post_url.split('/')[-1]
                    release_post_id(post_id, team_name)
                    return False, None, None, f"评论被限制: {restriction_text}"

                try:
                    post_buttons = driver.find_elements(By.XPATH, "//div[@aria-label='发布']")
                    if post_buttons:
                        for button in post_buttons:
                            if button.is_displayed():
                                print("找到发布按钮")
                                driver.execute_script("arguments[0].click();", button)
                                print("评论发布中，等待5秒...")
                                time.sleep(5)

                                # 最终检测限制
                                is_restricted, restriction_text = detect_sending_restriction(driver)
                                if is_restricted:
                                    print(f"🚫 最终检测到评论限制: {restriction_text}")
                                    update_account_status(account_number, "封禁")
                                    post_id = post_url.split('/')[-1]
                                    release_post_id(post_id, team_name)
                                    return False, None, None, f"评论被限制: {restriction_text}"

                                break
                except Exception as e:
                    print(f"点击发布按钮时出错: {e}")

                print("评论已发布")
                # 更新账号状态为正常
                update_account_status(account_number, "正常")
                user_link, user_id = extract_user_link(driver)
                return True, user_link, user_id, "已评论"

            except Exception as e:
                print(f"输入评论时出错: {e}")
                return False, None, None, f"输入评论失败: {e}"
        else:
            print("未找到评论框")
            return False, None, None, "未找到评论框"

        print("自动评论失败")
        user_link, user_id = extract_user_link(driver)
        return False, user_link, user_id, "评论失败"

    except Exception as e:
        print(f"发表评论时出错: {e}")
        return False, None, None, f"评论过程出错: {e}"


def send_message_and_add_friend(driver, user_link, user_id, message_text, account_number, team_name):
    """访问用户主页，发送私信并添加好友 - 增强版本，添加限制检测"""
    if not user_link or not user_id:
        print("没有找到用户链接或ID，无法发送私信和添加好友")
        return False, False, "没有用户链接", "没有用户链接"

    if is_user_messaged(account_number, user_id):
        print(f"用户ID {user_id} 已经收到过账号 {account_number} 的私信，跳过")
        return True, True, "用户已收到私信", "用户已收到私信"

    print(f"正在访问用户主页: {user_link}")
    try:
        driver.get(user_link)
        time.sleep(3)

        # 检测发送限制
        is_restricted, restriction_text = detect_sending_restriction(driver)
        if is_restricted:
            print(f"🚫 检测到私信限制: {restriction_text}")
            update_account_status(account_number, "封禁")
            return False, False, f"私信被限制: {restriction_text}", f"私信被限制: {restriction_text}"

        original_window = driver.current_window_handle

        try:
            close_buttons = driver.find_elements(By.XPATH, "//div[@aria-label='关闭聊天窗口']")
            if close_buttons:
                for button in close_buttons:
                    if button.is_displayed():
                        print("找到现有聊天窗口，正在关闭...")
                        driver.execute_script("arguments[0].click();", button)
                        time.sleep(2)
                        print("已关闭现有聊天窗口")
        except Exception as e:
            print(f"检查聊天窗口时出错: {e}")

        message_sent = False
        message_status = "未发送"

        try:
            message_buttons = driver.find_elements(By.XPATH, "//span[text()='发消息' or text()='Message']")
            if not message_buttons:
                message_buttons = driver.find_elements(By.XPATH,
                                                       "//div[contains(text(), '发消息') or contains(text(), 'Message')]")

            if message_buttons:
                for button in message_buttons:
                    if button.is_displayed():
                        print("找到发消息按钮")
                        driver.execute_script("arguments[0].click();", button)
                        time.sleep(3)
                        break

                time.sleep(3)

                # 检查是否出现发送限制
                is_restricted, restriction_text = detect_sending_restriction(driver)
                if is_restricted:
                    print(f"🚫 点击发消息后检测到限制: {restriction_text}")
                    update_account_status(account_number, "封禁")
                    return False, False, f"私信被限制: {restriction_text}", f"私信被限制: {restriction_text}"

                if message_text.startswith("Whatsapp:"):
                    message_text = message_text.replace("Whatsapp:", "", 1).strip()

                message_input = None
                message_input_selectors = [
                    "//div[@aria-label='发消息' and @contenteditable='true']",
                    "//div[@aria-label='Message' and @contenteditable='true']",
                    "//div[@role='textbox' and @contenteditable='true']",
                    "//div[@data-lexical-editor='true']"
                ]

                for selector in message_input_selectors:
                    try:
                        message_input = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, selector))
                        )
                        if message_input.is_displayed():
                            print(f"找到消息输入框: {selector}")
                            break
                    except:
                        continue

                if not message_input:
                    for selector in message_input_selectors:
                        elements = driver.find_elements(By.XPATH, selector)
                        for element in elements:
                            if element.is_displayed():
                                message_input = element
                                print(f"找到消息输入框: {selector}")
                                break
                        if message_input:
                            break

                if message_input:
                    print(f"准备发送消息预览: {message_text[:30]}...")

                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", message_input)
                        actions = ActionChains(driver)
                        actions.move_to_element(message_input).click().perform()
                        time.sleep(1)

                        driver.execute_script("arguments[0].innerHTML = '';", message_input)
                        time.sleep(1)

                        # 逐字符输入
                        lines = message_text.split('\n')

                        if lines:
                            for char in lines[0]:
                                actions = ActionChains(driver)
                                actions.send_keys(char).perform()
                                time.sleep(random.uniform(0.05, 0.15))

                        for line in lines[1:]:
                            actions = ActionChains(driver)
                            actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
                            time.sleep(0.5)

                            for char in line:
                                actions = ActionChains(driver)
                                actions.send_keys(char).perform()
                                time.sleep(random.uniform(0.05, 0.15))

                        time.sleep(2)
                        print("已完成文本输入，准备发送...")

                        # 发送前检查限制
                        is_restricted, restriction_text = detect_sending_restriction(driver)
                        if is_restricted:
                            print(f"🚫 发送前检测到限制: {restriction_text}")
                            update_account_status(account_number, "封禁")
                            return False, False, f"私信被限制: {restriction_text}", f"私信被限制: {restriction_text}"

                        send_buttons = driver.find_elements(By.XPATH,
                                                            "//div[@aria-label='按 Enter 键发送' or @aria-label='Press Enter to send']")
                        if send_buttons:
                            for button in send_buttons:
                                if button.is_displayed():
                                    print("找到发送按钮，点击...")
                                    driver.execute_script("arguments[0].click();", button)
                                    break
                            else:
                                print("未找到发送按钮，使用Enter键发送...")
                                message_input.send_keys(Keys.ENTER)
                        else:
                            print("未找到发送按钮，使用Enter键发送...")
                            message_input.send_keys(Keys.ENTER)

                        time.sleep(5)

                        # 发送后检查限制
                        is_restricted, restriction_text = detect_sending_restriction(driver)
                        if is_restricted:
                            print(f"🚫 发送后检测到限制: {restriction_text}")
                            update_account_status(account_number, "封禁")
                            return False, False, f"私信被限制: {restriction_text}", f"私信被限制: {restriction_text}"

                        message_sent = True
                        message_status = "已私信"
                        print("消息发送完成！")

                        # 更新账号状态为正常
                        update_account_status(account_number, "正常")
                        add_messaged_user(account_number, user_id)

                    except Exception as e:
                        print(f"发送消息失败: {e}")
                        message_status = f"发送失败: {e}"

                else:
                    print("未找到消息输入框")
                    message_status = "未找到消息输入框"
            else:
                print("未找到发消息按钮")
                message_status = "未找到发消息按钮"
        except Exception as e:
            print(f"发送私信时出错: {e}")
            message_status = f"私信过程出错: {e}"

        if len(driver.window_handles) > 1:
            driver.switch_to.window(original_window)

        try:
            current_url = driver.current_url
            if user_link not in current_url:
                print("重新访问用户主页...")
                driver.get(user_link)
                time.sleep(3)
        except:
            driver.get(user_link)
            time.sleep(3)

        friend_added = False
        friend_status = "未添加"

        # 如果私信因为限制失败，friend_status也设为相同状态
        if "被限制" in message_status:
            friend_status = message_status
        else:
            try:
                add_friend_buttons = driver.find_elements(By.XPATH,
                                                          "//span[text()='添加好友' or text()='加为好友' or text()='Add Friend']")
                if not add_friend_buttons:
                    add_friend_buttons = driver.find_elements(By.XPATH,
                                                              "//div[contains(text(), '添加好友') or contains(text(), '加为好友') or contains(text(), 'Add Friend')]")

                if add_friend_buttons:
                    for button in add_friend_buttons:
                        if button.is_displayed():
                            print("找到添加好友按钮")
                            driver.execute_script("arguments[0].click();", button)
                            time.sleep(3)
                            print("已点击添加好友按钮")
                            friend_added = True
                            friend_status = "已发送申请"
                            break
                else:
                    print("未找到添加好友按钮")
                    friend_status = "未找到添加好友按钮"
            except Exception as e:
                print(f"添加好友时出错: {e}")
                friend_status = f"添加好友出错: {e}"

        return message_sent, friend_added, message_status, friend_status

    except Exception as e:
        print(f"访问用户主页时出错: {e}")
        return False, False, f"访问主页出错: {e}", f"访问主页出错: {e}"


def submit_work_log(post_url, customer_profile_url, customer_nickname, comment_status, message_status, friend_status,
                    sender_account_name):
    """提交工作日志"""
    try:
        print("正在提交工作日志...")

        request_url = 'http://47.95.157.46:8520/api/work-logs/submit'
        headers = {'Content-Type': 'application/json'}
        json_param = {
            "postUrl": post_url,
            "customerProfileUrl": customer_profile_url,
            "customerNickname": customer_nickname,
            "commentStatus": comment_status,
            "messageStatus": message_status,
            "friendStatus": friend_status,
            "senderAccountName": sender_account_name
        }
        data = json.dumps(json_param)

        # 禁用代理
        proxies = {
            'http': None,
            'https': None
        }

        response = requests.post(request_url, headers=headers, data=data, proxies=proxies, timeout=10)

        print(f"工作日志API响应状态码: {response.status_code}")
        print(f"工作日志API响应内容: {response.text}")

        if response.status_code == 200:
            print("✅ 工作日志提交成功")
            return True
        else:
            print(f"工作日志提交失败，状态码: {response.status_code}")

        print("工作日志提交失败")
        return False

    except Exception as e:
        print(f"提交工作日志时出错: {str(e)}")
        return False


def setup_sender_driver(cookies_file=None):
    """创建发送消息用的浏览器实例"""
    print("创建发送消息浏览器实例...")

    try:
        chrome_options = Options()

        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-infobars")
        chrome_options.add_experimental_option("detach", True)

        chrome_options.add_argument("--window-name=Facebook-Sender")

        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        user_agent = random.choice(USER_AGENTS)
        chrome_options.add_argument(f"--user-agent={user_agent}")

        # 使用独立的用户数据目录
        user_data_dir = USER_DATA_DIRS["sender"]
        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

        driver = webdriver.Chrome(options=chrome_options)
        print("发送消息浏览器实例创建成功")

        if cookies_file:
            print("加载cookies...")
            load_cookies(driver, cookies_file)

        return driver

    except Exception as e:
        print(f"创建发送消息浏览器实例失败: {e}")
        return None


def run_facebook_automation_task(driver, account_number, team_name):
    """执行单个Facebook自动化任务 - 增强版本，添加账号状态检查"""

    # 检查账号是否可以发送任务
    print(f"🔍 检查账号 {account_number} 是否可以执行发送任务...")
    can_send = check_account_can_send(account_number)

    if not can_send:
        print(f"❌ 账号 {account_number} 当前不能执行发送任务（被封禁且未超过10小时）")
        return {"success": False, "performed_actions": False, "reason": "账号被封禁"}

    print(f"✅ 账号 {account_number} 可以执行发送任务")

    # 获取Facebook任务
    task_info = get_facebook_task(team_name)

    if not task_info:
        print("获取Facebook任务失败，无任务可执行")
        return {"success": False, "performed_actions": False, "reason": "无任务"}

    post_content = task_info['post_content']
    post_id = task_info['post_id']
    post_url = task_info['post_url']

    try:
        print(f"\n开始分析帖子: ID={post_id}")
        print(f"帖子URL: {post_url}")
        print(f"帖子内容预览: {post_content[:150]}...")

        # 检查是否已经处理过 - 使用发送模块专用的防重复机制
        if is_task_processed(post_id):
            print(f"[发送模块] 帖子ID {post_id} 已处理过，跳过重复处理")
            return {"success": True, "performed_actions": False, "reason": "已处理过"}

        # AI分析
        print("正在使用AI系统分析帖子内容...")

        # 使用完整的提示词
        prompt_template = """请对以下帖子做角色与需求识别，判断发帖人是否属于有"从中国采购并发货到国外"需求的**潜在代发客户**

输入内容：
文本内容：{用户原文}

分析要求：
1. **角色判定**  
 - **买家 (潜在客户)**：明确表达「想买 / 需要某商品」并希望从中国发往其所在国或目标市场。典型用语：  
   - looking for / need / want to buy … shipped from China (to …)  
   - can anyone source / dropship / supply … to my country …  
 - **卖家 / 供应商 / 服务商**：宣传自己能生产、定制、备货、仓储或提供国际物流。典型用语：  
   - customization available / we offer / I supply / in-stock / wholesale price / ready to ship  
   - international shipping available / dropshipping service provided / DM me for quotation

2. 语义理解：
 识别用户是否有从中国采购并发货到国外的需求
 判断用户是否在寻找代发服务、代理或供应商
 提取目标市场(如欧洲、意大利等)和期望的物流时效等信息

3. 关键词识别：
 检测文本中是否包含代发业务相关关键词或语义表达

4. 需求判断：
 分析用户是否需要中国代发/代sourcing服务
 给出明确的判断理由

目标客户特征：

关键表达（满足任一即可）：
1. 代发/代购需求表达：
 looking for products to ship to [国家/地区]
 need items shipped from China to [国家/地区]
 looking for Chinese supplier/agent
 need shipping service from China
 sourcing products from China

2. 物流需求表达：
 shipping to Europe/Italy/[其他国家]
 ship to Europe/Italy/[其他国家]
 deliver to my country
 shipping time to [国家/地区]
 fast delivery to [国家/地区]

3. 核心业务关键词：
 supplier, dropshipping, agent, shipping, China, products
 sourcing, purchasing, delivery, shipment
 order fulfillment, warehousing, inventory
 logistics, international shipping, customs clearance

识别特征：
用户表达了从中国购买/发送商品的意愿
提到特定产品类型及目的地市场
可能询问价格、物流时效或服务细节
通常使用英语或简单直接的表达方式

输出格式：

判定结果：
是/否：该用户是/不是中国代发潜在客户

判定依据：
1. 关键表达匹配：找出文本中的代发相关表达
2. 需求明确性：用户是否明确表达了代发需求
3. 市场匹配度：目标市场是否符合我们的服务范围(如欧洲)
4. 产品可行性：所提及产品是否适合代发业务

示例分析：
输入：I am looking for heated slippers to ship to Europe
判定结果：是
判定依据：
关键表达匹配：包含"ship to Europe"，明确表达物流需求
需求明确性：明确表达了需要产品(heated slippers)并发往欧洲
市场匹配度：目标市场为欧洲，符合代发业务范围
产品可行性：加热拖鞋属于常见消费品，适合代发业务"""

        prompt = prompt_template.replace("{用户原文}", post_content)
        ai_response = analyze_with_ai(prompt)

        # 解析分析结果
        is_target = parse_analysis_result(ai_response)

        print("\n==== AI分析结果 ====")
        print(ai_response)
        print(f"\n分析结论: {'符合目标条件' if is_target else '不符合目标条件'}")

        # 只有当分析结果为"是"时才继续执行操作
        if is_target:
            print("\n✅ 帖子符合目标条件，开始访问链接并执行评论和私信操作...")

            # 访问帖子页面
            print(f"访问帖子URL: {post_url}")
            driver.get(post_url)
            print("等待页面加载完毕...")
            time.sleep(5)

            # 从API获取评论和私信内容
            comment_text, message_text = get_comment_and_message_content(team_name)

            if not comment_text:
                comment_text = "Great post! I'd love to connect and discuss more."

            if not message_text:
                message_text = "Hello, I saw your post and would like to connect. I'm interested in learning more about your business."

            # 执行评论
            comment_success, user_link, user_id, comment_status = comment_on_post(
                driver, post_url, comment_text, account_number, team_name)

            # 检查评论是否因为限制失败
            if "被限制" in comment_status:
                print(f"❌ 评论被限制，任务结束: {comment_status}")
                add_processed_task(post_id)
                return {"success": False, "performed_actions": True, "reason": comment_status}

            if not comment_success:
                print("自动评论失败")
                if not user_link or not user_id:
                    print("未能提取用户链接或ID，无法继续执行")
                    add_processed_task(post_id)
                    return {"success": False, "performed_actions": True, "reason": "评论失败且无法提取用户信息"}
            else:
                print("评论成功，等待一段时间后继续...")
                wait_time = random.uniform(10, 20)
                print(f"等待 {wait_time:.1f} 秒...")
                time.sleep(wait_time)

            # 发送私信并添加好友
            if user_link and user_id:
                print("开始发送私信和添加好友...")

                message_sent, friend_added, message_status, friend_status = send_message_and_add_friend(
                    driver, user_link, user_id, message_text, account_number, team_name
                )

                # 检查私信是否因为限制失败
                if "被限制" in message_status:
                    print(f"❌ 私信被限制，任务结束: {message_status}")
                    add_processed_task(post_id)
                    return {"success": False, "performed_actions": True, "reason": message_status}

                if message_sent:
                    print("已成功发送私信")
                else:
                    print("发送私信失败")

                if friend_added:
                    print("已成功添加好友")
                else:
                    print("添加好友失败")

                # 获取客户昵称
                customer_nickname = get_customer_nickname(driver)

                # 提交工作日志
                print("正在提交工作日志...")
                log_success = submit_work_log(
                    post_url=post_url,
                    customer_profile_url=user_link,
                    customer_nickname=customer_nickname,
                    comment_status=comment_status,
                    message_status=message_status,
                    friend_status=friend_status,
                    sender_account_name=account_number
                )

                if log_success:
                    print("✅ 工作日志提交成功")
                else:
                    print("❌ 工作日志提交失败")

                # 标记任务为已处理
                add_processed_task(post_id)
                return {"success": True, "performed_actions": True, "reason": "完整执行完成"}
            else:
                print("没有获取到用户链接或ID，无法发送私信和添加好友")
                add_processed_task(post_id)  # 标记为已处理
                return {"success": False, "performed_actions": True, "reason": "无用户链接"}
        else:
            print("\n❌ 帖子不符合目标条件，跳过所有后续操作")
            add_processed_task(post_id)  # 标记为已处理
            return {"success": True, "performed_actions": False, "reason": "不符合目标条件"}

    except Exception as e:
        print(f"执行任务过程中出错: {e}")
        # 如果错误是由于账号限制导致的，释放帖子ID
        if "被限制" in str(e):
            release_post_id(post_id, team_name)
        add_processed_task(post_id)  # 即使出错也标记为已处理，避免重复尝试
        return {"success": False, "performed_actions": False, "reason": f"执行过程出错: {e}"}


def start_facebook_sender_automation(team_name, account_number):
    """启动Facebook发送消息自动化程序 - 增强版本"""
    print("=" * 50)
    print(f"Facebook发送消息自动化工具")
    print(f"团队名称: {team_name}")
    print(f"账号: {account_number}")
    print("=" * 50)

    driver = None
    cookies_file_path = None
    task_count = 0
    successful_tasks = 0
    failed_tasks = 0
    consecutive_failures = 0
    cookie_retry_count = 0
    max_cookie_retries = 3

    def setup_browser_with_cookie(account):
        """设置浏览器并加载Cookie"""
        nonlocal cookies_file_path, cookie_retry_count

        print(f"正在为账号 {account} 设置浏览器...")

        # 获取Cookie URL
        cookie_url = get_cookie_url_from_api(account)
        if not cookie_url:
            print("❌ 获取Cookie URL失败")
            return None, None

        # 下载Cookie文件
        print("正在下载Cookie文件...")
        new_cookies_file_path = download_cookies_from_url(cookie_url)
        if not new_cookies_file_path:
            print("❌ Cookie文件下载失败")
            return None, None

        print(f"✅ Cookie文件下载成功: {new_cookies_file_path}")

        # 清理旧的Cookie文件
        if cookies_file_path and cookies_file_path != new_cookies_file_path:
            try:
                os.remove(cookies_file_path)
                print(f"已清理旧Cookie文件: {cookies_file_path}")
            except:
                pass

        cookies_file_path = new_cookies_file_path

        # 创建浏览器实例
        new_driver = setup_sender_driver(cookies_file_path)

        if not new_driver:
            print("浏览器设置失败")
            return None, None

        # 检查登录状态
        if not check_facebook_login_status(new_driver):
            print("⚠️ Cookie可能无效，尝试重新加载...")
            load_cookies(new_driver, cookies_file_path)
            time.sleep(3)

        if check_facebook_login_status(new_driver):
            print("✅ 登录状态确认成功")
            cookie_retry_count = 0
            return new_driver, cookies_file_path
        else:
            print("❌ 登录状态确认失败")
            # 报告Cookie失效
            report_cookie_status(account, "失效")
            if new_driver:
                new_driver.quit()
            return None, None

    try:
        # 加载发送模块的已处理任务数据
        print("\n📊 加载已处理任务数据...")
        load_processed_tasks()

        # 初始设置浏览器
        driver, cookies_file_path = setup_browser_with_cookie(account_number)
        if not driver:
            print("初始浏览器设置失败，程序终止")
            return

        print("\n开始持续监控任务...")

        while True:
            print(f"\n===== 任务轮次 #{task_count + 1} =====")
            print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"已完成: 成功 {successful_tasks} / 失败 {failed_tasks}")

            # 执行任务
            task_result = run_facebook_automation_task(driver, account_number, team_name)

            # 检查任务结果
            task_success = task_result["success"]
            performed_actions = task_result["performed_actions"]
            task_reason = task_result["reason"]

            if task_success:
                print("✅ 任务执行成功")
                successful_tasks += 1
                consecutive_failures = 0

                if performed_actions:
                    # 执行了实际操作，等待10秒
                    wait_time = 10
                    print(f"✅ 任务执行成功，已执行网页操作，等待 {wait_time} 秒后继续下一个任务...")
                else:
                    # 只是分析了帖子但没有执行实际操作，不等待
                    wait_time = 0
                    print(f"✅ 任务执行成功，仅进行了分析（{task_reason}），立即继续下一个任务...")
            else:
                print("❌ 任务执行失败或无可用任务")
                failed_tasks += 1

                if task_reason == "无任务":
                    # 无任务时固定等待10秒
                    wait_time = 10
                    print(f"⚠️ 暂无任务，等待 {wait_time} 秒后继续检查...")
                    consecutive_failures = 0
                elif task_reason == "账号被封禁":
                    # 账号被封禁时等待较长时间
                    wait_time = 3600  # 等待1小时
                    print(f"⚠️ 账号被封禁，等待 {wait_time // 60} 分钟后重新检查...")
                    consecutive_failures = 0
                elif "被限制" in task_reason:
                    # 账号被限制时等待较长时间
                    wait_time = 3600  # 等待1小时
                    print(f"⚠️ 账号被限制，等待 {wait_time // 60} 分钟后重新检查...")
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        print(f"⚠️ 连续失败 {consecutive_failures} 次，延长等待时间")
                        wait_time = min(3600 * consecutive_failures, 7200)  # 最多等待2小时
                        print(f"等待 {wait_time // 60:.1f} 分钟后继续下一个任务...")
                    else:
                        wait_time = random.randint(60, 120)  # 1-2分钟
                        print(f"等待 {wait_time} 秒后继续下一个任务...")

            # 等待
            if wait_time > 0:
                time.sleep(wait_time)

            task_count += 1

            # 每10个任务后显示统计信息
            if task_count % 10 == 0:
                print("\n" + "=" * 50)
                print(f"📊 任务统计 (已执行 {task_count} 个任务)")
                print(f"成功率: {successful_tasks / task_count * 100:.1f}%")
                print(f"失败率: {failed_tasks / task_count * 100:.1f}%")
                print("=" * 50)

    except KeyboardInterrupt:
        print("\n🛑 检测到键盘中断，程序即将退出...")
    except Exception as e:
        print(f"❌ 执行过程中出错: {e}")
    finally:
        print("\n🧹 程序退出，正在清理资源...")
        if driver:
            try:
                driver.quit()
            except:
                pass
        print("✅ 浏览器已关闭")

        # 清理下载的cookie文件
        if cookies_file_path:
            try:
                os.remove(cookies_file_path)
                print(f"✅ 已清理下载的cookie文件: {cookies_file_path}")
            except:
                pass

        print(f"📊 任务执行统计:")
        print(f"   - 总任务数: {task_count}")
        print(f"   - 成功任务: {successful_tasks}")
        print(f"   - 失败任务: {failed_tasks}")
        if task_count > 0:
            print(f"   - 成功率: {successful_tasks / task_count * 100:.1f}%")


# ============ 主程序 ============
def main(team_name):
    """
    主程序函数 - 只需要输入team_name

    Args:
        team_name (str): 团队名称
    """
    print("=" * 80)
    print("Facebook自动化完整程序")
    print("=" * 80)
    print(f"团队名称: {team_name}")
    print("正在获取团队详情...")
    print("=" * 80)

    # 获取团队详情
    team_accounts = get_team_detail(team_name)
    if not team_accounts:
        print("❌ 获取团队详情失败，程序退出")
        return

    print(f"✅ 成功获取团队详情，共 {len(team_accounts)} 个账号")

    # 显示账号信息
    monitor_accounts = []
    sender_accounts = []

    for account in team_accounts:
        account_name = account.get('account')
        role_type = account.get('role_type')
        cookie_status = account.get('cookie_status')

        print(f"账号: {account_name}, 角色: {role_type}, Cookie状态: {cookie_status}")

        if role_type == "获取":
            monitor_accounts.append(account)
        elif role_type == "发送":
            sender_accounts.append(account)

    print(f"\n监控账号数量: {len(monitor_accounts)}")
    print(f"发送账号数量: {len(sender_accounts)}")

    # 启动各个模块
    threads = []

    try:
        # 启动监控模块线程
        for account in monitor_accounts:
            account_name = account.get('account')
            cookie_status = account.get('cookie_status')

            print(f"\n🚀 启动监控模块 - 账号: {account_name}")

            # 检查cookie状态
            if cookie_status == "失效" or cookie_status is None:
                print(f"⚠️ 账号 {account_name} cookie状态为 {cookie_status}，需要重新登录")

                # 执行登录流程
                email = account.get('account')
                password = account.get('password')
                totp_secret = account.get('secret_key', '').replace(' ', '')

                print(f"🔐 开始自动登录账号: {account_name}")

                login_driver = ensure_facebook_login(email, password, totp_secret)
                if login_driver:
                    print("✅ 登录成功，保存cookies...")

                    # 保存cookies到文件
                    cookie_file = save_cookies_to_file(login_driver, account_name)
                    if cookie_file:
                        # 上传到COS
                        print("📤 上传cookies到云存储...")
                        upload_url = upload_to_cos(cookie_file, f"{account_name}_cookies.json")
                        if upload_url:
                            # 更新cookie URL到API
                            print("🔄 更新cookie URL到系统...")
                            update_success = update_cookie_url(upload_url, account_name)
                            if update_success:
                                print("✅ Cookie更新完成")
                            else:
                                print("❌ Cookie URL更新失败")
                        else:
                            print("❌ Cookie文件上传失败")
                    else:
                        print("❌ Cookie文件保存失败")

                    # 关闭登录用的浏览器
                    login_driver.quit()
                else:
                    print(f"❌ 账号 {account_name} 登录失败")
                    continue

            # 启动监控线程
            monitor_thread = threading.Thread(
                target=start_facebook_group_monitor,
                args=(account_name, team_name),
                daemon=True,
                name=f"Monitor-{account_name}"
            )
            monitor_thread.start()
            threads.append(monitor_thread)

            # 添加一些延迟，避免同时启动太多浏览器
            time.sleep(5)

        # 启动发送模块线程
        for account in sender_accounts:
            account_name = account.get('account')
            cookie_status = account.get('cookie_status')

            print(f"\n🚀 启动发送模块 - 账号: {account_name}")

            # 检查cookie状态
            if cookie_status == "失效" or cookie_status is None:
                print(f"⚠️ 账号 {account_name} cookie状态为 {cookie_status}，需要重新登录")

                # 执行登录流程
                email = account.get('account')
                password = account.get('password')
                totp_secret = account.get('secret_key', '').replace(' ', '')

                print(f"🔐 开始自动登录账号: {account_name}")

                login_driver = ensure_facebook_login(email, password, totp_secret)
                if login_driver:
                    print("✅ 登录成功，保存cookies...")

                    # 保存cookies到文件
                    cookie_file = save_cookies_to_file(login_driver, account_name)
                    if cookie_file:
                        # 上传到COS
                        print("📤 上传cookies到云存储...")
                        upload_url = upload_to_cos(cookie_file, f"{account_name}_cookies.json")
                        if upload_url:
                            # 更新cookie URL到API
                            print("🔄 更新cookie URL到系统...")
                            update_success = update_cookie_url(upload_url, account_name)
                            if update_success:
                                print("✅ Cookie更新完成")
                            else:
                                print("❌ Cookie URL更新失败")
                        else:
                            print("❌ Cookie文件上传失败")
                    else:
                        print("❌ Cookie文件保存失败")

                    # 关闭登录用的浏览器
                    login_driver.quit()
                else:
                    print(f"❌ 账号 {account_name} 登录失败")
                    continue

            # 启动发送线程
            sender_thread = threading.Thread(
                target=start_facebook_sender_automation,
                args=(team_name, account_name),
                daemon=True,
                name=f"Sender-{account_name}"
            )
            sender_thread.start()
            threads.append(sender_thread)

            # 添加一些延迟，避免同时启动太多浏览器
            time.sleep(5)

        print(f"\n✅ 所有模块已启动，共 {len(threads)} 个线程")
        print("程序正在运行中，按 Ctrl+C 退出...")

        # 等待所有线程
        while True:
            time.sleep(10)
            # 检查是否有线程停止
            active_threads = [t for t in threads if t.is_alive()]
            if len(active_threads) != len(threads):
                print(f"⚠️ 检测到有线程停止，活跃线程: {len(active_threads)}/{len(threads)}")
            time.sleep(50)  # 每分钟检查一次

    except KeyboardInterrupt:
        print("\n🛑 检测到键盘中断，程序即将退出...")
    except Exception as e:
        print(f"❌ 程序运行时出错: {e}")
    finally:
        print("\n🧹 程序退出，正在清理资源...")
        print("✅ 程序已退出")


if __name__ == "__main__":
    # 使用示例
    team_name = input("请输入团队名称: ").strip()

    if team_name:
        main(team_name)
    else:
        print("❌ 团队名称不能为空")
        exit(1)