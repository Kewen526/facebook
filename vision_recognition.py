#!/usr/bin/env python3
"""
独立视觉识别脚本 - 无任何外部依赖（除 zhipuai、requests）
功能：传入图片 + 提示词 → 返回识别结果
特性：自动获取key、多key轮换、拉黑名单、必须返回结果

安装依赖：pip install zhipuai requests

用法：
    # 命令行
    python vision_recognition.py /path/to/image.jpg "这张图片里有什么？"
    python vision_recognition.py https://example.com/img.jpg "请描述图片内容"

    # 代码中调用
    from vision_recognition import recognize
    result = recognize("/path/to/image.jpg", "这张图片里有什么？")
"""

import os
import re
import sys
import time
import base64
import logging
import threading
import requests
from typing import Optional
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# 配置区 - 所有配置都在这里，不依赖任何外部文件
# ============================================================

# Key获取API地址（自动从服务端拉取所有可用的ZhipuAI密钥）
ZHIPU_KEY_API = "http://47.95.157.46:8520/api/zhipuai_key"

# 视觉模型名称
VISION_MODEL = "glm-4v-flash"

# 拉黑默认时长（秒）
BLACKLIST_DURATION_SECONDS = 600  # 10分钟

# 必须出结果 - 最大轮询轮数（每轮遍历所有可用key）
MAX_ROUNDS = 3

# 每轮之间的等待基数（第N轮等待 N*ROUND_WAIT_BASE 秒）
ROUND_WAIT_BASE = 10

# 获取不到key时的等待时间（秒）
NO_KEY_WAIT = 5

# API调用超时（秒）
API_TIMEOUT = 120

# ============================================================
# 日志配置
# ============================================================

logger = logging.getLogger("vision_recognition")


def _setup_logging():
    """配置日志（仅在直接运行脚本时调用）"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )


# ============================================================
# 拉黑名单管理
# ============================================================

_blacklist_lock = threading.Lock()
_blacklisted_keys = {}  # {key: expiry_time} 被拉黑的key及其解封时间


def blacklist_key(api_key: str, duration: int = BLACKLIST_DURATION_SECONDS):
    """
    将API key加入拉黑名单

    Args:
        api_key: 要拉黑的key
        duration: 拉黑时长（秒），默认600秒（10分钟）
    """
    with _blacklist_lock:
        expiry = datetime.now() + timedelta(seconds=duration)
        _blacklisted_keys[api_key] = expiry
        masked = _mask_key(api_key)
        logger.warning(f"API key {masked} 已拉黑 {duration}秒，解封时间: {expiry.strftime('%H:%M:%S')}")


def unblacklist_key(api_key: str):
    """手动将API key移出拉黑名单"""
    with _blacklist_lock:
        if api_key in _blacklisted_keys:
            del _blacklisted_keys[api_key]
            logger.info("API key 已手动解除拉黑")


def is_blacklisted(api_key: str) -> bool:
    """检查API key是否在拉黑名单中（过期自动解除）"""
    with _blacklist_lock:
        if api_key not in _blacklisted_keys:
            return False
        expiry = _blacklisted_keys[api_key]
        if datetime.now() >= expiry:
            del _blacklisted_keys[api_key]
            logger.info("API key 拉黑已到期，自动解除")
            return False
        return True


def get_blacklist_status() -> dict:
    """
    获取当前拉黑名单状态

    Returns:
        {masked_key: "剩余 N 秒", ...}
    """
    with _blacklist_lock:
        now = datetime.now()
        status = {}
        expired_keys = []
        for key, expiry in _blacklisted_keys.items():
            if now >= expiry:
                expired_keys.append(key)
            else:
                remaining = int((expiry - now).total_seconds())
                status[_mask_key(key)] = f"剩余 {remaining} 秒"
        for k in expired_keys:
            del _blacklisted_keys[k]
        return status


def clear_blacklist() -> int:
    """
    清空拉黑名单

    Returns:
        被清除的key数量
    """
    with _blacklist_lock:
        count = len(_blacklisted_keys)
        _blacklisted_keys.clear()
        logger.info(f"已清空拉黑名单，共移除 {count} 个key")
        return count


def _mask_key(api_key: str) -> str:
    """脱敏显示key"""
    if len(api_key) > 12:
        return api_key[:8] + "..." + api_key[-4:]
    return "***"


# ============================================================
# API Key 自动获取
# ============================================================

def get_keys() -> list:
    """
    从服务端自动获取所有ZhipuAI API密钥，并过滤掉被拉黑的key

    Returns:
        可用的key列表
    """
    try:
        proxies = {'http': None, 'https': None}
        response = requests.post(ZHIPU_KEY_API, proxies=proxies, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get("success") and "data" in result:
                all_keys = [item["key"] for item in result["data"]]
                available = [k for k in all_keys if not is_blacklisted(k)]
                logger.info(
                    f"获取到 {len(all_keys)} 个密钥，"
                    f"可用 {len(available)} 个，"
                    f"拉黑 {len(all_keys) - len(available)} 个"
                )
                return available
    except Exception as e:
        logger.error(f"获取密钥失败: {e}")
    return []


# ============================================================
# 图片处理
# ============================================================

def encode_image(image_input: str) -> str:
    """
    将图片转为API可接受的格式

    支持三种输入：
    1. 本地文件路径：/path/to/image.jpg → data:image/jpeg;base64,...
    2. URL：https://xxx/img.jpg → 原样返回
    3. base64字符串：已编码的直接返回，裸base64自动补前缀

    Args:
        image_input: 图片路径、URL或base64字符串

    Returns:
        处理后的图片字符串

    Raises:
        ValueError: 无法处理的输入
        FileNotFoundError: 本地文件不存在
    """
    # 已经是完整的data URI
    if image_input.startswith("data:image"):
        return image_input

    # URL - 直接返回（ZhipuAI支持URL输入）
    if image_input.startswith("http://") or image_input.startswith("https://"):
        return image_input

    # 本地文件
    if os.path.exists(image_input):
        with open(image_input, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")

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

    # 可能是裸base64字符串（长度>500且不是路径也不是URL）
    if len(image_input) > 500:
        return f"data:image/jpeg;base64,{image_input}"

    raise ValueError(f"无法处理的图片输入（文件不存在或格式不识别）: {image_input[:100]}...")


# ============================================================
# 视觉识别API调用（单次）
# ============================================================

def call_vision_api(
    api_key: str,
    image_input: str,
    prompt: str,
    max_retries: int = 1,
    temperature: float = 0.7
) -> Optional[str]:
    """
    调用ZhipuAI视觉模型API（单个key，单次调用）

    失败时会根据错误类型自动拉黑key：
    - 认证失败 → 拉黑30分钟
    - 频率限制 → 拉黑5分钟
    - 模型不可用 → 拉黑1小时
    - 网络错误 → 不拉黑，重试

    Args:
        api_key: API密钥
        image_input: 图片（已处理的格式）
        prompt: 提示词
        max_retries: 网络错误最大重试次数
        temperature: 温度参数

    Returns:
        识别结果文本，失败返回None
    """
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # 处理图片
    try:
        image_content = encode_image(image_input)
    except Exception as e:
        logger.error(f"图片处理失败: {e}")
        return None

    for attempt in range(max_retries):
        try:
            logger.info(f"视觉识别调用尝试 {attempt + 1}/{max_retries}")

            # 禁用代理，确保直连
            os.environ['NO_PROXY'] = '*'
            os.environ['HTTP_PROXY'] = ''
            os.environ['HTTPS_PROXY'] = ''

            from zhipuai import ZhipuAI
            client = ZhipuAI(api_key=api_key, timeout=API_TIMEOUT, max_retries=2)

            # 构建视觉模型消息（图片+文本多模态输入）
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
                    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                    logger.info(f"视觉识别成功 (尝试 {attempt + 1})")
                    return cleaned

        except ImportError:
            logger.error("zhipuai 库未安装，请运行: pip install zhipuai")
            break
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"视觉识别失败 (尝试 {attempt + 1}/{max_retries}): {e}")

            # API key无效 → 拉黑30分钟
            if any(kw in error_msg for kw in ['api key', 'auth', 'unauthorized', 'invalid']):
                blacklist_key(api_key, duration=1800)
                logger.warning("API key认证失败，已拉黑30分钟")
                return None

            # 频率限制/额度用尽 → 拉黑5分钟
            if any(kw in error_msg for kw in ['rate limit', 'quota', 'concurrent', '并发', 'too many']):
                blacklist_key(api_key, duration=300)
                logger.warning("API key频率受限，已拉黑5分钟")
                return None

            # 模型不支持 → 拉黑1小时
            if any(kw in error_msg for kw in ['model not found', 'not support', 'invalid model']):
                blacklist_key(api_key, duration=3600)
                logger.warning("模型不可用，已拉黑1小时")
                return None

            # 网络/超时错误 → 重试（不拉黑）
            if any(kw in error_msg for kw in ['connection', 'timeout', 'network', 'ssl']):
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 2
                    logger.info(f"网络错误，{wait}秒后重试...")
                    time.sleep(wait)
                    continue

    return None


# ============================================================
# 核心识别函数（必须出结果）
# ============================================================

def recognize_image(
    image_input: str,
    prompt: str,
    temperature: float = 0.7,
    max_rounds: int = MAX_ROUNDS
) -> str:
    """
    视觉识别 - 多key轮换，必须出结果

    策略（与原项目 ai_analyzer.analyze_with_ai 完全一致）：
    1. 从服务端获取所有可用key（自动过滤已拉黑的）
    2. 逐个key尝试调用视觉API
    3. 某个key成功 → 立即返回结果
    4. 某个key失败 → 根据错误类型自动拉黑，继续下一个key
    5. 本轮所有key失败 → 等待后进入下一轮（重新获取key，拉黑的会被跳过）
    6. 所有轮次都失败 → 返回失败提示（保证不会返回None）

    Args:
        image_input: 图片输入（本地路径 / URL / base64字符串）
        prompt: 识别提示词
        temperature: 温度参数（默认0.7）
        max_rounds: 最大轮询轮数（默认3）

    Returns:
        识别结果字符串（保证有返回值）
    """
    logger.info("开始视觉识别...")

    for round_num in range(max_rounds):
        keys = get_keys()
        if not keys:
            logger.warning(f"第{round_num + 1}轮: 未获取到可用密钥，等待{NO_KEY_WAIT}秒重试...")
            time.sleep(NO_KEY_WAIT)
            continue

        logger.info(f"第{round_num + 1}轮: 尝试 {len(keys)} 个可用密钥")
        for i, key in enumerate(keys):
            logger.info(f"  密钥 {i + 1}/{len(keys)}...")
            result = call_vision_api(key, image_input, prompt, max_retries=1, temperature=temperature)
            if result:
                logger.info("视觉识别成功！")
                return result

        # 本轮所有key都失败，等待后重试
        if round_num < max_rounds - 1:
            wait_time = (round_num + 1) * ROUND_WAIT_BASE
            logger.warning(f"第{round_num + 1}轮所有密钥失败，等待{wait_time}秒后进入下一轮...")
            time.sleep(wait_time)

    # 所有轮次都失败 - 保证返回结果
    logger.error("所有API密钥在多轮尝试后均不可用")
    return "识别失败：所有API密钥在多轮尝试后均不可用，无法完成视觉识别。请稍后重试。"


# ============================================================
# 并发投票识别（三选二，必须出结果）
# ============================================================

def recognize_image_concurrent(
    image_input: str,
    prompt: str,
    num_votes: int = 3,
    temperature: float = 0.7
) -> tuple:
    """
    并发多次视觉识别，投票取最佳结果

    Args:
        image_input: 图片输入
        prompt: 识别提示词
        num_votes: 并发次数（默认3）
        temperature: 温度参数

    Returns:
        (final_result, combined_response, all_responses)
    """
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

    # 取最长的有效结果（更详细的回答通常更准确）
    valid = [r for r in all_responses if not r.startswith("识别失败") and not r.startswith("识别异常")]
    if valid:
        final_result = max(valid, key=len)
    else:
        final_result = all_responses[0] if all_responses else "识别失败：无有效结果"

    combined = f"=== 并发识别完成 ({len(valid)}/{num_votes} 成功) ===\n\n"
    for i, resp in enumerate(all_responses):
        status = "成功" if resp not in all_responses or (not resp.startswith("识别失败") and not resp.startswith("识别异常")) else "失败"
        status = "成功" if not resp.startswith("识别失败") and not resp.startswith("识别异常") else "失败"
        combined += f"--- 第{i+1}轮 ({status}) ---\n{resp}\n\n"

    return final_result, combined, all_responses


# ============================================================
# 最简调用接口
# ============================================================

def recognize(image: str, prompt: str, concurrent: bool = False) -> str:
    """
    一行代码调用视觉识别

    Args:
        image: 图片（本地路径 / URL / base64）
        prompt: 提示词（你想让AI识别什么）
        concurrent: 是否使用并发投票模式（更准确但更慢）

    Returns:
        识别结果字符串

    示例:
        from vision_recognition import recognize

        # 本地图片
        result = recognize("/path/to/image.jpg", "这张图片里有什么？")

        # URL图片
        result = recognize("https://example.com/image.jpg", "请描述图片内容")

        # 并发投票（更准确）
        result = recognize("/path/to/image.jpg", "识别图片中的文字", concurrent=True)
    """
    if concurrent:
        final_result, _, _ = recognize_image_concurrent(image, prompt)
        return final_result
    else:
        return recognize_image(image, prompt)


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    _setup_logging()

    if len(sys.argv) < 3:
        print("=" * 60)
        print("视觉识别脚本 - 独立运行，自动获取key")
        print("=" * 60)
        print()
        print("用法:")
        print("  python vision_recognition.py <图片路径或URL> <提示词>")
        print()
        print("示例:")
        print("  python vision_recognition.py ./photo.jpg '这张图片里有什么？'")
        print("  python vision_recognition.py https://xxx.com/img.jpg '请描述图片内容'")
        print("  python vision_recognition.py ./receipt.png '识别图片中的文字和金额'")
        print()
        print("依赖安装:")
        print("  pip install zhipuai requests")
        sys.exit(1)

    image_path = sys.argv[1]
    user_prompt = sys.argv[2]

    print(f"\n图片: {image_path}")
    print(f"提示词: {user_prompt}")

    blacklist_info = get_blacklist_status()
    if blacklist_info:
        print(f"拉黑名单: {blacklist_info}")
    else:
        print("拉黑名单: 空")

    print("-" * 50)
    print("开始识别...\n")

    result = recognize(image_path, user_prompt)

    print("=" * 50)
    print("识别结果:")
    print("=" * 50)
    print(result)
