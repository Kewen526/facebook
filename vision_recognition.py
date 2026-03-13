"""
视觉识别模块 - 基于ZhipuAI GLM-4V视觉模型
功能：传入图片 + 提示词 → 返回识别结果
特性：自动获取key、多key轮换、拉黑名单、必须返回结果
"""

import os
import re
import time
import base64
import logging
import threading
import requests
from typing import Optional, Union
from datetime import datetime, timedelta

from config import ZHIPU_KEY_API

logger = logging.getLogger(__name__)

# ============ 视觉模型配置 ============
VISION_MODEL = "glm-4v-flash"  # ZhipuAI视觉模型

# ============ 拉黑名单管理 ============
_blacklist_lock = threading.Lock()
_blacklisted_keys = {}  # {key: expiry_time} 被拉黑的key及其解封时间

BLACKLIST_DURATION_SECONDS = 600  # 拉黑时长（默认10分钟）


def blacklist_key(api_key: str, duration: int = BLACKLIST_DURATION_SECONDS):
    """将API key加入拉黑名单"""
    with _blacklist_lock:
        expiry = datetime.now() + timedelta(seconds=duration)
        _blacklisted_keys[api_key] = expiry
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        logger.warning(f"API key {masked} 已拉黑，解封时间: {expiry.strftime('%H:%M:%S')}")


def unblacklist_key(api_key: str):
    """手动将API key移出拉黑名单"""
    with _blacklist_lock:
        if api_key in _blacklisted_keys:
            del _blacklisted_keys[api_key]
            logger.info(f"API key 已手动解除拉黑")


def is_blacklisted(api_key: str) -> bool:
    """检查API key是否在拉黑名单中"""
    with _blacklist_lock:
        if api_key not in _blacklisted_keys:
            return False
        expiry = _blacklisted_keys[api_key]
        if datetime.now() >= expiry:
            # 已过期，自动解除拉黑
            del _blacklisted_keys[api_key]
            logger.info(f"API key 拉黑已到期，自动解除")
            return False
        return True


def get_blacklist_status() -> dict:
    """获取拉黑名单状态"""
    with _blacklist_lock:
        now = datetime.now()
        status = {}
        expired_keys = []
        for key, expiry in _blacklisted_keys.items():
            if now >= expiry:
                expired_keys.append(key)
            else:
                masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
                remaining = (expiry - now).total_seconds()
                status[masked] = f"剩余 {int(remaining)} 秒"
        # 清理过期的
        for k in expired_keys:
            del _blacklisted_keys[k]
        return status


def clear_blacklist():
    """清空拉黑名单"""
    with _blacklist_lock:
        count = len(_blacklisted_keys)
        _blacklisted_keys.clear()
        logger.info(f"已清空拉黑名单，共移除 {count} 个key")
        return count


# ============ Key获取 ============

def get_vision_keys() -> list:
    """获取ZhipuAI API密钥（与ai_analyzer共用同一key池）"""
    try:
        proxies = {'http': None, 'https': None}
        response = requests.post(ZHIPU_KEY_API, proxies=proxies, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get("success") and "data" in result:
                keys = [item["key"] for item in result["data"]]
                # 过滤掉被拉黑的key
                available_keys = [k for k in keys if not is_blacklisted(k)]
                logger.info(f"获取到 {len(keys)} 个密钥，可用 {len(available_keys)} 个（拉黑 {len(keys) - len(available_keys)} 个）")
                return available_keys
    except Exception as e:
        logger.error(f"获取密钥失败: {e}")
    return []


# ============ 图片处理 ============

def encode_image_to_base64(image_input: str) -> str:
    """
    将图片转为base64编码
    支持：本地文件路径、URL、已编码的base64字符串
    """
    # 如果已经是base64字符串
    if image_input.startswith("data:image"):
        return image_input
    if len(image_input) > 500 and not os.path.exists(image_input) and not image_input.startswith("http"):
        # 可能是裸base64
        return f"data:image/jpeg;base64,{image_input}"

    # 如果是URL，直接返回（ZhipuAI支持URL）
    if image_input.startswith("http://") or image_input.startswith("https://"):
        return image_input

    # 本地文件
    if os.path.exists(image_input):
        with open(image_input, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")

        # 根据文件扩展名判断MIME类型
        ext = os.path.splitext(image_input)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        mime = mime_map.get(ext, "image/jpeg")
        return f"data:{mime};base64,{img_data}"

    raise ValueError(f"无法处理的图片输入: {image_input[:100]}...")


# ============ 视觉识别API调用 ============

def call_vision_api(
    api_key: str,
    image_input: str,
    prompt: str,
    max_retries: int = 1,
    temperature: float = 0.7
) -> Optional[str]:
    """
    调用ZhipuAI视觉模型API

    Args:
        api_key: API密钥
        image_input: 图片（本地路径/URL/base64）
        prompt: 提示词
        max_retries: 最大重试次数
        temperature: 温度参数

    Returns:
        识别结果文本，失败返回None
    """
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # 处理图片
    try:
        image_content = encode_image_to_base64(image_input)
    except Exception as e:
        logger.error(f"图片处理失败: {e}")
        return None

    for attempt in range(max_retries):
        try:
            logger.info(f"视觉识别调用尝试 {attempt + 1}/{max_retries}")

            os.environ['NO_PROXY'] = '*'
            os.environ['HTTP_PROXY'] = ''
            os.environ['HTTPS_PROXY'] = ''

            from zhipuai import ZhipuAI
            client = ZhipuAI(api_key=api_key, timeout=120, max_retries=2)

            # 构建视觉模型消息（图片+文本）
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_content
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]

            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=4096,
                top_p=0.95
            )

            if response.choices and len(response.choices) > 0:
                content = response.choices[0].message.content
                if content:
                    # 清除思考标签
                    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                    logger.info(f"视觉识别成功 (尝试 {attempt + 1})")
                    return cleaned

        except ImportError as e:
            logger.error(f"ZhipuAI库导入失败: {e}")
            break
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"视觉识别失败 (尝试 {attempt + 1}/{max_retries}): {e}")

            # API key无效 → 拉黑
            if any(kw in error_msg for kw in ['api key', 'auth', 'unauthorized', 'invalid']):
                blacklist_key(api_key, duration=1800)  # 拉黑30分钟
                logger.warning(f"API key认证失败，已拉黑30分钟")
                return None

            # 频率限制/额度用尽 → 拉黑
            if any(kw in error_msg for kw in ['rate limit', 'quota', 'concurrent', '并发', 'too many']):
                blacklist_key(api_key, duration=300)  # 拉黑5分钟
                logger.warning(f"API key频率受限，已拉黑5分钟")
                return None

            # 模型不支持/参数错误 → 拉黑（不要继续浪费）
            if any(kw in error_msg for kw in ['model not found', 'not support', 'invalid model']):
                blacklist_key(api_key, duration=3600)  # 拉黑1小时
                logger.warning(f"模型不可用，已拉黑1小时")
                return None

            # 网络/超时错误 → 重试
            if any(kw in error_msg for kw in ['connection', 'timeout', 'network', 'ssl']):
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                    continue

    return None


# ============ 核心识别函数（必须出结果） ============

def recognize_image(
    image_input: str,
    prompt: str,
    temperature: float = 0.7,
    max_rounds: int = 3
) -> str:
    """
    视觉识别 - 必须出结果

    使用多key轮询策略，最多轮询max_rounds轮所有可用key，
    确保一定返回结果。与ai_analyzer.analyze_with_ai完全一致的逻辑。

    Args:
        image_input: 图片输入（本地路径/URL/base64字符串）
        prompt: 识别提示词（你希望AI从图片中识别什么）
        temperature: 温度参数（默认0.7）
        max_rounds: 最大轮询轮数（默认3）

    Returns:
        识别结果字符串（保证有返回，即使全部失败也会返回默认提示）
    """
    logger.info("开始视觉识别...")

    for round_num in range(max_rounds):
        keys = get_vision_keys()
        if not keys:
            logger.warning(f"第{round_num + 1}轮: 未获取到可用密钥，等待5秒重试...")
            time.sleep(5)
            continue

        logger.info(f"第{round_num + 1}轮: 尝试 {len(keys)} 个可用密钥")
        for i, key in enumerate(keys):
            logger.info(f"尝试第{i + 1}个密钥...")
            result = call_vision_api(key, image_input, prompt, max_retries=1, temperature=temperature)
            if result:
                logger.info("视觉识别成功")
                return result

        # 本轮所有key都失败，等待后重试
        if round_num < max_rounds - 1:
            wait_time = (round_num + 1) * 10
            logger.warning(f"第{round_num + 1}轮所有密钥失败，等待{wait_time}秒后重试...")
            time.sleep(wait_time)

    # 所有轮次都失败
    logger.error("所有API密钥在多轮尝试后均不可用，返回默认结果")
    return "识别失败：所有API密钥在多轮尝试后均不可用，无法完成视觉识别。请稍后重试。"


# ============ 并发投票识别（三选二，必须出结果） ============

def recognize_image_concurrent(
    image_input: str,
    prompt: str,
    num_votes: int = 3,
    temperature: float = 0.7
) -> tuple:
    """
    并发三选二投票视觉识别

    多次并发调用视觉识别，取多数结果作为最终答案。
    适用于需要高准确率的场景。

    Args:
        image_input: 图片输入
        prompt: 识别提示词
        num_votes: 投票次数（默认3）
        temperature: 温度参数

    Returns:
        (final_result, combined_response, all_responses)
        - final_result: 出现次数最多的结果
        - combined_response: 汇总报告
        - all_responses: 各轮原始结果列表
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def single_vote():
        return recognize_image(image_input, prompt, temperature=temperature)

    all_responses = []

    with ThreadPoolExecutor(max_workers=num_votes) as executor:
        futures = [executor.submit(single_vote) for _ in range(num_votes)]
        for future in as_completed(futures):
            try:
                response = future.result()
                all_responses.append(response)
            except Exception as e:
                logger.error(f"并发投票异常: {e}")
                all_responses.append(f"识别异常: {e}")

    # 取最长的非错误结果作为最终结果（更详细的回答通常更准确）
    valid_responses = [r for r in all_responses if not r.startswith("识别失败") and not r.startswith("识别异常")]
    if valid_responses:
        final_result = max(valid_responses, key=len)
    else:
        final_result = all_responses[0] if all_responses else "识别失败：无有效结果"

    combined_response = f"=== 并发识别完成 ({len(valid_responses)}/{num_votes} 成功) ===\n\n"
    for i, resp in enumerate(all_responses):
        status = "成功" if not resp.startswith("识别失败") and not resp.startswith("识别异常") else "失败"
        combined_response += f"--- 第{i+1}轮 ({status}) ---\n{resp}\n\n"

    return final_result, combined_response, all_responses


# ============ 便捷调用接口 ============

def recognize(image: str, prompt: str, concurrent: bool = False) -> str:
    """
    最简调用接口

    Args:
        image: 图片（本地路径 / URL / base64）
        prompt: 提示词（你想让AI识别什么）
        concurrent: 是否使用并发投票模式（更准确但更慢）

    Returns:
        识别结果字符串

    使用示例:
        # 基础用法 - 传入图片路径和提示词
        result = recognize("/path/to/image.jpg", "这张图片里有什么？")

        # 传入URL
        result = recognize("https://example.com/image.jpg", "请描述图片内容")

        # 并发投票模式（更准确）
        result = recognize("/path/to/image.jpg", "识别图片中的文字", concurrent=True)
    """
    if concurrent:
        final_result, _, _ = recognize_image_concurrent(image, prompt)
        return final_result
    else:
        return recognize_image(image, prompt)


# ============ 命令行测试 ============

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )

    if len(sys.argv) < 3:
        print("用法: python vision_recognition.py <图片路径或URL> <提示词>")
        print("示例: python vision_recognition.py /path/to/image.jpg '这张图片里有什么？'")
        print("示例: python vision_recognition.py https://example.com/img.jpg '请描述图片内容'")
        sys.exit(1)

    image_path = sys.argv[1]
    user_prompt = sys.argv[2]

    print(f"\n图片: {image_path}")
    print(f"提示词: {user_prompt}")
    print(f"拉黑名单: {get_blacklist_status() or '空'}")
    print("-" * 50)

    result = recognize(image_path, user_prompt)
    print(f"\n识别结果:\n{result}")
