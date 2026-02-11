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
- ⚠️ **卖家在寻找客户**："寻找更多客户" / "looking for customers" / "扩大规模" / "expand business" → 这是卖家行为，不是买家！
- 提供WhatsApp/微信等联系方式招揽生意 → 卖家
- 提供批量/批发销售："bulk quantity" / "wholesale available" / "retail and wholesale" / "available in wholesale"
- 品牌名称+联系方式组合：通常是卖家在推广自己的品牌
- 邀请联系购买："DM for orders" / "contact for pricing" / "direct message for bulk" / "please contact wholesalers"

❌ **与代发业务完全无关的帖子 = 判"否"**：
- 交友/征婚/社交帖："looking for partner/relationship" / "寻找伴侣"
- 个人生活分享、娱乐、新闻
- 讨论市场趋势、分享行业新闻/教程
- 没有明确的个人采购意图
- 正文内容过短、无实质含义、只有产品名称无采购表达

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
4. 综合结论：产品广告帖，不是目标客户

示例5（否 - 卖家寻找客户）：
输入：另一个 eBay 账号——今日特卖！\n模式：两步代发货（库存模式）——按百分比计酬\n投资回报率：50% – 60%\n👉正在寻找更多客户以扩大规模！\n📱WhatsApp：+92348 8663404
判定结果：否
判定依据：
1. 角色判定：卖家/服务商 - "寻找更多客户"说明是卖家在招揽生意
2. 关键表达匹配："寻找更多客户" + "扩大规模" + 提供WhatsApp联系方式 = 典型卖家招客话术
3. 需求明确性：发帖人是在推广自己的代发服务，不是在寻找供应商
4. 综合结论：卖家在找客户，不是目标客户

示例6（否 - 交友/社交帖）：
输入：I'm looking for a good partner for me, I don't care about your finances, I have a business, the most important thing is to be loyal
判定结果：否
判定依据：
1. 角色判定：非商业帖 - 这是一条交友/征婚帖
2. 关键表达匹配："looking for a good partner" 指的是寻找伴侣，不是商业合作伙伴
3. 需求明确性：与中国采购代发完全无关
4. 综合结论：交友帖，不是目标客户

示例7（否 - 批发卖家）：
输入：Quality jacket's hoodies available in wholesale and retail shipping worldwide
判定结果：否
判定依据：
1. 角色判定：卖家/供应商 - 在宣传自己的服装产品
2. 关键表达匹配："available" + "wholesale and retail" + "shipping worldwide" = 产品供应商广告
3. 需求明确性：发帖人是在出售商品，而非寻找供应商
4. 综合结论：批发卖家广告帖，不是目标客户

示例8（否 - 鞋类卖家/零售商）：
输入：DRIECT MESSAGE FOR BULK QUANTITY. !!! Nagina Footwear. What's app 0346-84 786 92 Please Contact wholesalers or Shopkeepers.
判定结果：否
判定依据：
1. 角色判定：卖家/供应商 - 在推广自己的鞋类产品并招揽批量订单
2. 关键表达匹配："DIRECT MESSAGE FOR BULK QUANTITY" + 品牌名 + WhatsApp联系方式 = 典型供应商招商话术
3. 需求明确性：发帖人是在销售产品，提供WhatsApp以接收订单
4. 综合结论：产品卖家推广帖，不是目标客户"""


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


# ============ 内容生成提示词 ============

COMMENT_PROMPT = """你是一位专业的跨境电商供应商助手，专门生成Facebook评论话术。

请生成1条Facebook评论：
- 简短自然，像真人在评论，不像广告
- 要让客户看到后有想回复或私信我的欲望
- 结尾引导客户回复我或私信我，例如："DM me if interested!" / "Let's work together!" / "Feel free to message me!"
- 不要问客户卖什么产品或针对什么市场
- 强调：代发货、无最低起订量、价格有竞争力、快速发货
- 可以提到：ERP系统、自动上传追踪号、售后服务、品牌定制包装
- 不需要加WhatsApp号码
- 不超过50字

直接输出1条评论，不需要解释。"""

DM_WITH_WHATSAPP_PROMPT = """你是一位专业的跨境电商供应商助手，专门生成Facebook私信话术。

我的WhatsApp账号：{whatsapp_number}

请生成1条Facebook私信：
- 友好自然，像真人发送，不像群发广告
- 要让客户看到后有强烈想回复的欲望
- 用提问句或悬念结尾，引发互动
- 强调：代发货代理、工厂价格、无最低起订量、快速全球发货
- 可以提到：ERP系统对接Shopify、自动上传追踪号、售后保障、品牌定制
- 结尾必须加：This is my WhatsApp. {whatsapp_number}
- 不超过100字

直接输出1条私信，不需要解释。"""

DM_WITHOUT_WHATSAPP_PROMPT = """你是一位专业的跨境电商供应商助手，专门生成Facebook私信话术。

请生成1条Facebook私信：
- 友好自然，像真人发送，不像群发广告
- 要让客户看到后有强烈想回复的欲望
- 用提问句或悬念结尾，引发互动
- 强调：代发货代理、工厂价格、无最低起订量、快速全球发货
- 可以提到：ERP系统对接Shopify、自动上传追踪号、售后保障、品牌定制
- 不超过80字

直接输出1条私信，不需要解释。"""


def generate_comment(post_content: str) -> Optional[str]:
    """生成评论内容"""
    logger.info("生成评论内容...")
    result = analyze_with_ai(COMMENT_PROMPT)
    if result and "判定结果" not in result:
        return result.strip()
    return result


def generate_dm_with_whatsapp(post_content: str, whatsapp_number: str) -> Optional[str]:
    """生成带WhatsApp的私信内容"""
    logger.info(f"生成私信内容 (WhatsApp: {whatsapp_number})...")
    prompt = DM_WITH_WHATSAPP_PROMPT.replace("{whatsapp_number}", whatsapp_number)
    result = analyze_with_ai(prompt)
    if result and "判定结果" not in result:
        return result.strip()
    return result


def generate_dm_without_whatsapp(post_content: str) -> Optional[str]:
    """生成不带WhatsApp的私信内容"""
    logger.info("生成私信内容 (无WhatsApp)...")
    result = analyze_with_ai(DM_WITHOUT_WHATSAPP_PROMPT)
    if result and "判定结果" not in result:
        return result.strip()
    return result
