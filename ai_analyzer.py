import os
import re
import time
import logging
import requests
from typing import Optional

from config import ZHIPU_KEY_API, ZHIPU_MODEL

logger = logging.getLogger(__name__)

# AI分析提示词模板
PROMPT_TEMPLATE = """请对以下帖子做角色与需求识别，判断发帖人是否属于有"从中国采购并发货到国外"需求的**潜在代发客户（买家）**。

⚠️ 核心判断原则：只有**买家**才是目标客户，**卖家/供应商/服务商/平台推广**统统判"否"。

⚠️ 重要提醒 - 区分正文与非正文：
输入内容中可能混入**小组名称**或**用户昵称**，这些不是帖子正文！
- 例如小组名 "Dropshipping Worldwide" 或用户名 "Shopify Expert John" 包含关键词，但不代表帖子内容与代发相关
- 你必须**只根据帖子正文内容**进行判定，忽略小组名称、用户昵称等非正文信息
- 如果正文内容很短、无实质含义或只是产品展示（无采购意图），应判"否"

输入内容：
文本内容：{用户原文}

分析要求：

**第一步：区分正文与非正文**
- 识别输入中哪些是帖子正文，哪些是小组名称/用户昵称/页面标题等非正文信息
- 只对帖子正文进行分析判定

**第二步：角色判定（最关键）**

✅ **买家（潜在客户）= 判"是"**：
发帖人自身有采购需求，想从中国买东西/找供应商/找代发服务。特征：
- 用第一人称表达需求："I'm looking for…" / "I need…" / "I want to buy…"
- 在寻找供应商/代理："looking for supplier" / "anyone can source…" / "need an agent"
- 明确提到要从中国发货到某国："ship from China to…" / "sourcing from China"
- 询问价格/物流："how much…" / "shipping cost to…" / "delivery time"

❌ **卖家/供应商/服务商 = 判"否"**：
发帖人自己在提供产品或服务，而非寻求采购。特征：
- 宣传自己的产品/服务："we offer" / "I supply" / "DM me" / "contact us"
- 展示库存/价格："in stock" / "wholesale price" / "MOQ" / "ready to ship"
- Dropshipping服务商宣传："we provide dropshipping service" / "fulfillment center"
- 物流公司推广："shipping line" / "freight forwarding" / "logistics solution"
- 电商平台/工具推广："Shopify expert" / "store setup" / "marketing service"
- 招聘/招商："hiring" / "join our team" / "become our agent"
- 展示成功案例/教程："how to start dropshipping" / "my store made $X"
- 产品广告/产品展示帖（只展示产品参数、价格，无采购意图）

❌ **信息分享/新闻/讨论帖 = 判"否"**：
- 分享行业新闻、经验、教程
- 讨论市场趋势
- 没有明确的个人采购意图

**第三步：需求验证**
即使角色是买家，还需确认：
1. 是否有从中国采购的意图（而非本地采购）
2. 是否有跨境发货需求（发往中国以外的国家）
3. 产品是否适合代发（非大宗原材料/危险品等）

输出格式（必须严格遵守）：

判定结果：是
或
判定结果：否

判定依据：
1. 角色判定：发帖人是买家/卖家/服务商/信息分享
2. 关键表达匹配：找出文本中的关键表达
3. 需求明确性：用户是否明确表达了从中国采购代发的需求
4. 综合结论：简要说明判定理由

示例1（是）：
输入：I am looking for heated slippers to ship to Europe, anyone know a good supplier in China?
判定结果：是
判定依据：
1. 角色判定：买家 - 第一人称表达采购需求
2. 关键表达匹配："looking for" + "ship to Europe" + "supplier in China"
3. 需求明确性：明确需要从中国采购加热拖鞋并发往欧洲
4. 综合结论：典型的中国代发潜在客户

示例2（否 - 卖家）：
输入：🔥 Shopify Experts Dropshipping | We help you build your store and source products from China. DM for free consultation!
判定结果：否
判定依据：
1. 角色判定：服务商 - 在推广自己的代发/建站服务
2. 关键表达匹配："We help you" + "DM for" = 典型卖家/服务商话术
3. 需求明确性：发帖人不是在寻找服务，而是在提供服务
4. 综合结论：服务商推广帖，不是目标客户

示例3（否 - 供应商）：
输入：High quality phone cases wholesale, MOQ 50pcs, ready to ship worldwide! Contact me for catalog.
判定结果：否
判定依据：
1. 角色判定：供应商/卖家 - 在展示自己的产品
2. 关键表达匹配："wholesale" + "MOQ" + "ready to ship" + "Contact me" = 供应商话术
3. 需求明确性：发帖人是在卖货，不是在找供应商
4. 综合结论：供应商推广帖，不是目标客户

示例4（否 - 小组名称干扰）：
输入：MAGA A/T TWO\nAll-terrain design\nAll-terrain four season tire pattern, more strengthful tread block design
判定结果：否
判定依据：
1. 角色判定：卖家 - 在展示轮胎产品参数
2. 关键表达匹配：仅有产品描述，无任何采购需求表达
3. 需求明确性：无从中国采购代发的需求
4. 综合结论：产品广告帖，不是目标客户"""


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
