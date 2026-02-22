import os
import re
import time
import logging
import requests
from typing import Optional

from config import ZHIPU_KEY_API, ZHIPU_MODEL

logger = logging.getLogger(__name__)

# AI分析提示词模板
PROMPT_TEMPLATE = """请对以下帖子做角色与需求识别，判断发帖人是否属于需要**采购实体商品/寻找产品供应商**的潜在客户。

⚠️ 核心原则：
- 只有**寻找产品货源/供应商/采购代理**的人才是目标客户
- 卖家/供应商/服务商/平台推广 → 判"否"
- 找服务（网站设计、引流、VA等）、找人（招聘freelancer）、找账号/数字资源 → 判"否"
- 只根据帖子正文内容判定，忽略小组名称、用户昵称等非正文信息
- 帖子可能使用任何语言

输入内容：
文本内容：{用户原文}

**第一步：排除非目标（命中任一即判"否"）**

❌ **卖家/供应商/服务商**：
- 宣传自己的产品/服务："we offer" / "I supply" / "DM me" / "contact us"
- 展示库存/价格："in stock" / "wholesale price" / "MOQ" / "ready to ship" / "available"
- 招揽客户："looking for customers/buyers/clients" / "寻找客户"
- 提供代发/物流服务："we provide dropshipping service" / "fulfillment center"
- 展示成功案例/教程："how to start dropshipping" / "my store made $X"
- 产品广告/展示帖：只展示产品参数、价格，无采购意图
- 卖家找渠道："We manufacture…" + "looking for partners to distribute"
- 留联系方式招商：品牌名+WhatsApp/微信号组合

❌ **找服务/找人/找数字资源（不是找产品货源）**：
- 找网站设计/建站/引流："need Shopify expert" / "design my website" / "bring traffic"
- 找freelancer/VA/员工："need freelancers" / "looking for VA" / "hiring"
- 找账号/数字资源："need eBay accounts" / "need Amazon account"
- 加密货币/金融交易："USDT needed" / "BTC buyer" / "crypto exchange"
- 找合作伙伴但不涉及产品采购

❌ **与商业完全无关**：
- 交友/征婚/社交帖
- 个人生活分享、娱乐、新闻
- 正文过短、无实质含义

**第二步：确认目标客户（满足任一即判"是"）**

✅ 发帖人在寻找**实体商品的货源或产品供应商**：

1. **寻找产品/货源**：
   - "looking for [具体产品]" / "I need [产品名]" / "where can I buy [产品]"
   - "I want to source/buy [商品]" / "anyone selling [商品]"
   - 明确提到要采购某种实体商品

2. **寻找产品供应商/采购代理**：
   - "looking for supplier" / "need a sourcing agent" / "looking for vendor"
   - 经营店铺并寻找产品供应商来进货
   - 有买家资源，在寻找能供货的供应商

3. **产品物流/发货需求**：
   - "ship [产品] to [国家]" / "need items shipped from China"
   - 表达了实体商品的跨境发货需求

4. **产品采购询问**：
   - 询问某类商品的价格/货源/物流时效
   - 提到1688/Alibaba/Taobao等平台的产品采购意图
   - 寻找代发服务来销售实体产品的买家

输出格式（必须严格遵守）：

判定结果：是/否

判定依据：
1. 角色判定：发帖人是买家/卖家/服务商/找服务/无关
2. 关键表达匹配：找出文本中的关键表达
3. 需求类型：产品采购 / 服务需求 / 招聘 / 数字资源 / 无关
4. 综合结论：简要说明判定理由

示例1（是 - 寻找产品供应商）：
输入：I am looking for heated slippers to ship to Europe, anyone know a good supplier?
判定结果：是
判定依据：
1. 角色判定：买家 - 第一人称寻找产品供应商
2. 关键表达匹配："looking for heated slippers" + "ship to Europe" + "good supplier"
3. 需求类型：产品采购 - 寻找实体商品货源
4. 综合结论：有明确产品采购需求的潜在客户

示例2（是 - 店主找供应商）：
输入：I am actively running a Shopify dropshipping store and looking for reliable suppliers for scalable products with long-term cooperation.
判定结果：是
判定依据：
1. 角色判定：买家 - 店主寻找产品供应商
2. 关键表达匹配："running a Shopify dropshipping store" + "looking for reliable suppliers" + "scalable products"
3. 需求类型：产品采购 - 经营店铺需要产品货源
4. 综合结论：Dropshipping店主寻找产品供应商，是目标客户

示例3（是 - 有买家资源找供应商）：
输入：Looking for Reliable Suppliers! We have active buyers in the USA and Europe — let's work together and grow!
判定结果：是
判定依据：
1. 角色判定：买家/分销商 - 有买家资源在找供应商供货
2. 关键表达匹配："Looking for Reliable Suppliers" + "active buyers in the USA and Europe"
3. 需求类型：产品采购 - 需要供应商提供产品给其买家
4. 综合结论：有销售渠道的分销商在找产品供应商，是目标客户

示例4（否 - 找服务不是找产品）：
输入：I need a shopify expert who design my website and bring traffic on website.
判定结果：否
判定依据：
1. 角色判定：服务采购方 - 找网站设计和引流服务
2. 关键表达匹配："need a shopify expert" + "design my website" + "bring traffic"
3. 需求类型：服务需求 - 不是产品采购
4. 综合结论：找建站/引流服务，不是找产品货源，不是目标客户

示例5（否 - 找账号/数字资源）：
输入：I am professional ebay VA, I need the ebay accounts on the percentage base
判定结果：否
判定依据：
1. 角色判定：服务从业者 - eBay VA找账号资源
2. 关键表达匹配："ebay VA" + "need ebay accounts" + "percentage base"
3. 需求类型：数字资源 - 不是实体产品采购
4. 综合结论：找eBay账号来管理，不是找产品货源，不是目标客户

示例6（否 - 招聘/找人）：
输入：Need Trustable Freelancers WhatsApp
判定结果：否
判定依据：
1. 角色判定：雇主 - 在招聘freelancer
2. 关键表达匹配："Need Trustable Freelancers"
3. 需求类型：招聘 - 不是产品采购
4. 综合结论：招人帖，不是找产品货源，不是目标客户

示例7（否 - 卖家推广）：
输入：🔥 We offer dropshipping service from China! DM for free consultation! WhatsApp: +86 138xxxx
判定结果：否
判定依据：
1. 角色判定：服务商 - 推广自己的代发服务
2. 关键表达匹配："We offer" + "DM for" + WhatsApp联系方式
3. 需求类型：无 - 发帖人在提供服务
4. 综合结论：服务商推广帖，不是目标客户"""


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


def call_zhipu_api(api_key: str, prompt: str, max_retries: int = 1, temperature: float = 0.7) -> Optional[str]:
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
                temperature=temperature,
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


def analyze_with_ai(prompt: str, temperature: float = 0.7) -> str:
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
            result = call_zhipu_api(key, prompt, max_retries=1, temperature=temperature)
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
        "是：该用户是潜在客户",
        "该用户是中国代发潜在客户",
        "该用户是潜在客户",
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
        "否：该用户不是潜在客户",
        "该用户不是中国代发潜在客户",
        "该用户不是潜在客户",
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


def analyze_post_concurrent(post_content: str, num_votes: int = 3) -> tuple:
    """并发三选二投票分析帖子，返回 (is_target, ai_response_combined, votes)"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    prompt = PROMPT_TEMPLATE.replace("{用户原文}", post_content)

    def single_vote():
        response = analyze_with_ai(prompt)
        is_target = parse_analysis_result(response)
        return is_target, response

    votes = []
    ai_responses = []

    with ThreadPoolExecutor(max_workers=num_votes) as executor:
        futures = [executor.submit(single_vote) for _ in range(num_votes)]
        for future in as_completed(futures):
            try:
                is_target, response = future.result()
                votes.append(is_target)
                ai_responses.append(response)
            except Exception as e:
                logger.error(f"并发投票异常: {e}")
                votes.append(False)
                ai_responses.append(f"投票异常: {e}")

    yes_count = sum(1 for v in votes if v)
    is_target = yes_count >= 2
    vote_summary = ", ".join(["是" if v else "否" for v in votes])
    final_label = "目标客户" if is_target else "非目标客户"

    ai_response = f"=== 投票结果: {vote_summary} → {final_label} ({yes_count}/{num_votes}) ===\n\n"
    for i, resp in enumerate(ai_responses):
        ai_response += f"--- 第{i+1}轮 ({('是' if votes[i] else '否')}) ---\n{resp}\n\n"

    return is_target, ai_response, votes


# ============ 内容生成提示词 ============

COMMENT_PROMPT = """You are a professional sourcing agent from China writing a Facebook comment. Be creative and vary your wording every time.
STRICT RULES: - Output ONLY in English. No Chinese characters allowed. - Do NOT include any name placeholder like [Name], [NAME], Hi [Name], etc. - Do NOT include any WhatsApp number. - Output ONLY the comment text. No explanation, no labels.
STYLE GUIDE (follow this tone closely): - Write as a supplier agent who negotiates factory prices and handles fulfillment - Sound professional and genuine, like a real person — not a spammy ad - Keep it 2-3 sentences, under 50 words - Core message: factory price negotiation + reliable order fulfillment + honest communication - End with a soft call-to-action like: "Let's begin with a low quote." / "Let's start with a competitive offer." / "DM me to get started." - Do NOT use hype words like "amazing", "incredible", "best ever" - Do NOT use bullet points or list format
GOOD EXAMPLES: - "We're a supplier agent focused on factory-level pricing and dependable delivery. We prefer clear and honest contact. Let's begin with a cost-effective offer." - "We help you deal with the factory to get the lowest cost and fast shipment. Honest communication reduces troubles. Let's begin with a low-budget offer." - "Hello, I am a Dropshipping agent from China. I can process orders for you, no MOQ, automatic upload tracking number, fast delivery worldwide. DM me if interested!"
Generate 1 comment now:"""

DM_WITH_WHATSAPP_PROMPT = """You are a professional sourcing agent from China writing a Facebook direct message. Be creative and vary your wording every time.
My WhatsApp: {whatsapp_number}
STRICT RULES: - Output ONLY in English. No Chinese characters allowed. - Do NOT include any name placeholder like [Name], [NAME], Hi [Name], Hello [Name], etc. Start directly without addressing a name. - The message MUST end with exactly: This is my WhatsApp. {whatsapp_number} - Output ONLY the message text. No explanation, no labels.
STYLE GUIDE (follow this tone closely): - Write as a supplier agent who negotiates factory prices and handles fulfillment - Sound professional and genuine, like a real person — not a spammy ad or mass message - Keep the body 2-4 sentences (before the WhatsApp line), total under 80 words - Core message: factory price negotiation + reliable fulfillment + honest/timely communication - You may mention: no MOQ, fast global shipping, ERP system, Shopify integration, auto tracking upload, after-sales support, custom branding/packaging - End the body with a soft engagement line, then add the WhatsApp line - Do NOT use hype words or excessive emojis - Do NOT use bullet points or list format
GOOD EXAMPLES: - "We're a supplier agent focused on factory-level pricing and dependable delivery. We prefer clear and honest contact. Let's begin with a cost-effective offer.\nThis is my WhatsApp. +86 158 5453 0808" - "Hi, I am a dropshipping supplier from China, I can provide products for you, fast shipping to all over the world, no MOQ. We have our own ERP system, which can link your shopify store, automatically process orders. Let's talk?\nThis is my WhatsApp. +86 158 5453 0808"
Generate 1 direct message now:"""

DM_WITHOUT_WHATSAPP_PROMPT = """You are a professional sourcing agent from China writing a Facebook direct message. Be creative and vary your wording every time.
STRICT RULES: - Output ONLY in English. No Chinese characters allowed. - Do NOT include any name placeholder like [Name], [NAME], Hi [Name], Hello [Name], etc. Start directly without addressing a name. - Do NOT include any WhatsApp number or phone number. - Output ONLY the message text. No explanation, no labels.
STYLE GUIDE (follow this tone closely): - Write as a supplier agent who negotiates factory prices and handles fulfillment - Sound professional and genuine, like a real person — not a spammy ad or mass message - Keep it 2-4 sentences, under 70 words - Core message: factory price negotiation + reliable fulfillment + honest/timely communication - You may mention: no MOQ, fast global shipping, ERP system, Shopify integration, auto tracking upload, after-sales support, custom branding/packaging - End with a question or engagement line to encourage reply, e.g.: "Do you have any products that need to be quoted?" / "Let's start with a competitive offer?" / "Shall we talk?" - Do NOT use hype words or excessive emojis - Do NOT use bullet points or list format
GOOD EXAMPLES: - "We're supplier agents helping you get the lowest possible cost and smooth logistics. Let's start with simple, honest communication and a good price." - "Hello, I am a dropshipping supplier from China with good price and fast shipping. We need a sincere shop owner, only in this way can we cooperate for a long time and grow together. If you're interested, let's talk?"
Generate 1 direct message now:"""


def _clean_generated_text(text: str) -> str:
    """清理AI生成内容：移除[Name]占位符、非BMP字符等"""
    if not text:
        return text
    # 移除 [Name] / [NAME] / Hi [Name], / Hello [Name], 等占位符
    text = re.sub(r'\[Name\]|\[NAME\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(?:Hi|Hello|Dear|Hey)\s+\[?\w*\]?,?\s*', '', text)
    # 移除非BMP字符（emoji等），防止ChromeDriver报错
    text = re.sub(r'[^\u0000-\uffff]', '', text)
    # 清理多余空格和空行
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def generate_comment(post_content: str) -> Optional[str]:
    """生成评论内容"""
    logger.info("生成评论内容...")
    result = analyze_with_ai(COMMENT_PROMPT, temperature=0.9)
    if result and "判定结果" not in result:
        return _clean_generated_text(result)
    return _clean_generated_text(result) if result else result


def generate_dm_with_whatsapp(post_content: str, whatsapp_number: str) -> Optional[str]:
    """生成带WhatsApp的私信内容"""
    logger.info(f"生成私信内容 (WhatsApp: {whatsapp_number})...")
    prompt = DM_WITH_WHATSAPP_PROMPT.replace("{whatsapp_number}", whatsapp_number)
    result = analyze_with_ai(prompt, temperature=0.9)
    if result and "判定结果" not in result:
        return _clean_generated_text(result)
    return _clean_generated_text(result) if result else result


def generate_dm_without_whatsapp(post_content: str) -> Optional[str]:
    """生成不带WhatsApp的私信内容"""
    logger.info("生成私信内容 (无WhatsApp)...")
    result = analyze_with_ai(DM_WITHOUT_WHATSAPP_PROMPT, temperature=0.9)
    if result and "判定结果" not in result:
        return _clean_generated_text(result)
    return _clean_generated_text(result) if result else result
