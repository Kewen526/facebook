import os
import re
import time
import logging
import requests
from typing import Optional

from config import ZHIPU_KEY_API, ZHIPU_MODEL

logger = logging.getLogger(__name__)

# AI分析提示词模板
PROMPT_TEMPLATE = """请对以下帖子做角色与需求识别，判断发帖人是否属于有"从中国采购并发货到国外"需求的**潜在代发客户**

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


def get_zhipu_keys():
    """获取ZhipuAI API密钥"""
    try:
        proxies = {'http': None, 'https': None}
        response = requests.post(ZHIPU_KEY_API, proxies=proxies, timeout=10)
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
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    for attempt in range(max_retries):
        try:
            logger.info(f"ZhipuAI调用尝试 {attempt + 1}/{max_retries}")

            os.environ['NO_PROXY'] = '*'
            os.environ['HTTP_PROXY'] = ''
            os.environ['HTTPS_PROXY'] = ''

            from zhipuai import ZhipuAI
            client = ZhipuAI(api_key=api_key, timeout=60, max_retries=2)

            response = client.chat.completions.create(
                model=ZHIPU_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=4096,
                top_p=0.95
            )

            if response.choices and len(response.choices) > 0:
                content = response.choices[0].message.content
                if content:
                    cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                    logger.info(f"ZhipuAI调用成功 (尝试 {attempt + 1})")
                    return cleaned

        except ImportError as e:
            logger.error(f"ZhipuAI库导入失败: {e}")
            break
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"ZhipuAI调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")

            if any(kw in error_msg for kw in ['api key', 'auth', 'unauthorized']):
                return None
            if any(kw in error_msg for kw in ['rate limit', 'quota', 'concurrent', '并发']):
                return None
            if any(kw in error_msg for kw in ['connection', 'timeout', 'network', 'ssl']):
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                    continue

    return None


def analyze_with_ai(prompt: str) -> str:
    """AI分析 - 使用ZhipuAI，多key轮询，必须出结果"""
    logger.info("开始AI分析...")

    max_rounds = 3  # 最多轮询3轮所有key

    for round_num in range(max_rounds):
        keys = get_zhipu_keys()
        if not keys:
            logger.warning(f"第{round_num + 1}轮: 未获取到密钥，等待5秒重试...")
            time.sleep(5)
            continue

        logger.info(f"第{round_num + 1}轮: 尝试 {len(keys)} 个密钥")
        for i, key in enumerate(keys):
            logger.info(f"尝试第{i + 1}个密钥...")
            result = call_zhipu_api(key, prompt, max_retries=1)
            if result:
                logger.info("AI分析成功")
                return result

        # 本轮所有key都失败，等待后重试
        if round_num < max_rounds - 1:
            wait_time = (round_num + 1) * 10
            logger.warning(f"第{round_num + 1}轮所有密钥失败，等待{wait_time}秒后重试...")
            time.sleep(wait_time)

    # 所有轮次都失败
    logger.error("所有API密钥在多轮尝试后均不可用，返回默认结果")
    return """判定结果：否

判定依据：
AI服务暂时不可用，无法进行准确分析。默认判定为非目标客户。"""


def parse_analysis_result(response: str) -> bool:
    """解析AI分析结果"""
    if not response:
        return False

    if "判定结果：是" in response:
        return True
    if "判定结果：否" in response:
        return False

    positive_patterns = [
        "是：该用户是中国代发潜在客户",
        "是：该用户是潜在代发客户",
        "该用户是中国代发潜在客户",
        "判定结果: 是",
        "判定结果 是",
        "结果：是",
        "结果: 是",
    ]
    for pattern in positive_patterns:
        if pattern in response:
            return True

    negative_patterns = [
        "否：该用户不是中国代发潜在客户",
        "否：该用户不是潜在代发客户",
        "该用户不是中国代发潜在客户",
        "判定结果: 否",
        "判定结果 否",
        "结果：否",
        "结果: 否",
    ]
    for pattern in negative_patterns:
        if pattern in response:
            return False

    return False


def analyze_post(post_content: str) -> tuple:
    """分析帖子内容，返回 (is_target, ai_response)"""
    prompt = PROMPT_TEMPLATE.replace("{用户原文}", post_content)
    response = analyze_with_ai(prompt)
    is_target = parse_analysis_result(response)
    return is_target, response
