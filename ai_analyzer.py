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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 核心原则（必须牢记）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **只有买家才是目标客户**：卖家/供应商/服务商/平台推广/中间商找客户 → 一律判"否"
2. **默认判"否"**：当信息不足、帖子含义模糊、无法确认买家身份时，默认判"否"。宁可漏判，不可错判
3. **只分析正文**：忽略小组名称、用户昵称、页面标题、话题标签等非正文信息
4. **语言无关**：帖子可能使用任何语言，判定逻辑适用于所有语言

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 分析流程（短路逻辑，命中即停）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**第一步：内容有效性筛查 → 无效则直接判"否"**

以下情况直接判"否"，无需进入后续步骤：
- 正文过短（< 10个实义词），无实质含义
- 只有产品名称/参数/图片描述，无任何人称表达
- 纯转发/分享链接，无个人表达
- 与商业完全无关（交友/征婚/社交/娱乐/新闻）
- OCR提取的水印、标签、广告文字（非发帖人原话）

**第二步：角色判定（最关键）→ 非买家则直接判"否"**

首先排除卖家/服务商/中间商，因为这类帖子远多于买家帖：

❌ 以下任一特征命中 → 判"否"（卖家/供应商/服务商）：

| 类别 | 典型特征 |
|------|----------|
| **自我推销** | "we offer/provide/supply"、"DM me/us"、"contact us"、"inbox me" |
| **展示库存** | "in stock"、"ready to ship"、"available"、"MOQ"、"wholesale price" |
| **招揽客户** | "looking for customers/buyers/clients"、"寻找客户"、"expand business"、"扩大规模" |
| **提供服务** | "we provide dropshipping/fulfillment/shipping service"、"free consultation" |
| **推广工具/平台** | "store setup"、"Shopify expert"、"marketing service"、"how to start…" |
| **留联系方式招商** | 品牌名+WhatsApp/微信号组合、"DM for orders/pricing"、"contact for catalog" |
| **批发销售** | "wholesale available"、"bulk quantity"、"retail and wholesale" |
| **物流推广** | "shipping line"、"freight forwarding"、"logistics solution" |
| **招聘/招商** | "hiring"、"join our team"、"become our agent/reseller" |
| **教程/案例** | "how to start dropshipping"、"my store made $X"、成功案例分享 |
| **产品广告** | 仅展示产品图片+参数+价格，无采购意图表达 |

⚠️ 特别注意：
- "looking for partner/collaboration" 如果上下文是卖家找分销渠道 → 判"否"
- "寻找合作" 如果发帖人同时展示了自己的产品/服务 → 判"否"（是卖家找渠道）
- 发帖人展示产品+问"anyone interested?" → 这是卖家在试探市场，判"否"

✅ 确认买家的核心特征（必须同时满足以下条件）：

条件A - **第一人称采购意图**（必须）：
- "I'm looking for…" / "I need…" / "I want to buy/source…" / "我想找…" / "我需要…"
- "Can anyone help me find…" / "Does anyone know where to get…"
- "Where can I buy…" / "Who sells…"

条件B - **中国采购或跨境发货相关**（至少命中一个）：
- 明确提到中国/China/1688/Alibaba/Taobao等中国平台
- 提到从中国发货到其他国家："ship from China to…" / "sourcing from China"
- 在代发/dropshipping相关讨论上下文中表达采购需求
- 询问中国供应商/代理："supplier in China" / "China agent" / "sourcing agent"

条件C - **产品适合代发**（排除不适合的）：
- 排除大宗原材料（钢材、矿石等）
- 排除危险品/违禁品
- 排除大型工业设备
- 一般消费品、电子产品、服装、家居用品等 → 适合

**三个条件全部满足 → 判"是"；任一不满足 → 判"否"**

**第三步：模糊情况兜底处理**

如果经过上述分析仍无法确定：
- 帖子既不像卖家也不像明确的买家 → 判"否"
- 表达了兴趣但未明确采购意图（如"this looks cool"）→ 判"否"
- 讨论市场趋势/行业话题但无个人采购需求 → 判"否"
- 问价但看不出是买家还是同行打探 → 判"否"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 输出格式（必须严格遵守）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

判定结果：是/否

判定依据：
1. 内容有效性：[正文是否有效/是否混入非正文内容]
2. 角色判定：[买家/卖家/服务商/无关] + 判定理由
3. 关键表达：[列出决定性的关键词句]
4. 需求匹配：[是否满足条件A+B+C]
5. 综合结论：[一句话总结]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 示例
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【示例1 - 是：典型买家】
输入：I am looking for heated slippers to ship to Europe, anyone know a good supplier in China?
判定结果：是
判定依据：
1. 内容有效性：正文有效，内容完整
2. 角色判定：买家 - 第一人称表达采购需求
3. 关键表达："I am looking for" + "ship to Europe" + "supplier in China"
4. 需求匹配：A(✓寻找产品) B(✓中国供应商+发欧洲) C(✓消费品适合代发)
5. 综合结论：典型的中国代发潜在客户

【示例2 - 否：服务商推广】
输入：🔥 Shopify Experts Dropshipping | We help you build your store and source products from China. DM for free consultation!
判定结果：否
判定依据：
1. 内容有效性：正文有效
2. 角色判定：服务商 - 推广建站+代发服务
3. 关键表达："We help you" + "DM for" = 服务商话术
4. 需求匹配：不适用（非买家）
5. 综合结论：服务商推广帖

【示例3 - 否：供应商广告】
输入：High quality phone cases wholesale, MOQ 50pcs, ready to ship worldwide! Contact me for catalog.
判定结果：否
判定依据：
1. 内容有效性：正文有效
2. 角色判定：供应商 - 展示产品+招揽订单
3. 关键表达："wholesale" + "MOQ" + "ready to ship" + "Contact me"
4. 需求匹配：不适用（非买家）
5. 综合结论：供应商广告帖

【示例4 - 否：产品展示（无采购意图）】
输入：MAGA A/T TWO\nAll-terrain design\nAll-terrain four season tire pattern, more strengthful tread block design
判定结果：否
判定依据：
1. 内容有效性：仅有产品参数描述，无人称表达
2. 角色判定：产品广告 - 纯产品展示
3. 关键表达：无采购相关表达
4. 需求匹配：条件A不满足
5. 综合结论：产品广告帖

【示例5 - 否：卖家找客户】
输入：另一个 eBay 账号——今日特卖！\n模式：两步代发货（库存模式）——按百分比计酬\n投资回报率：50% – 60%\n👉正在寻找更多客户以扩大规模！\n📱WhatsApp：+92348 8663404
判定结果：否
判定依据：
1. 内容有效性：正文有效
2. 角色判定：卖家/服务商 - "寻找更多客户" = 卖家招客
3. 关键表达："寻找更多客户" + "扩大规模" + WhatsApp联系方式
4. 需求匹配：不适用（非买家）
5. 综合结论：卖家招客帖

【示例6 - 否：交友帖】
输入：I'm looking for a good partner for me, I don't care about your finances, I have a business, the most important thing is to be loyal
判定结果：否
判定依据：
1. 内容有效性：正文有效，但非商业内容
2. 角色判定：非商业帖 - 交友/征婚
3. 关键表达："partner" = 伴侣，"loyal" = 忠诚，与商业无关
4. 需求匹配：与代发业务完全无关
5. 综合结论：交友帖

【示例7 - 否：批发卖家】
输入：Quality jacket's hoodies available in wholesale and retail shipping worldwide
判定结果：否
判定依据：
1. 内容有效性：正文有效
2. 角色判定：卖家 - 宣传自己的产品
3. 关键表达："available" + "wholesale and retail" + "shipping worldwide"
4. 需求匹配：不适用（非买家）
5. 综合结论：批发卖家广告

【示例8 - 否：模糊帖 - "找合作"但实为卖家】
输入：We manufacture premium leather bags in Guangzhou. Looking for partners to distribute in Europe and US. WhatsApp: +86 138xxxx
判定结果：否
判定依据：
1. 内容有效性：正文有效
2. 角色判定：卖家/制造商 - "We manufacture" + 找分销渠道
3. 关键表达："manufacture" + "looking for partners to distribute" = 卖家找渠道
4. 需求匹配：不适用（非买家）
5. 综合结论：制造商找分销渠道，非买家

【示例9 - 是：非英语买家】
输入：Je cherche un fournisseur en Chine pour des accessoires de téléphone, livraison en France. Qui peut m'aider?
判定结果：是
判定依据：
1. 内容有效性：正文有效（法语）
2. 角色判定：买家 - 第一人称寻找供应商
3. 关键表达："Je cherche"(我在找) + "fournisseur en Chine"(中国供应商) + "livraison en France"(发往法国)
4. 需求匹配：A(✓) B(✓中国供应商+发法国) C(✓手机配件适合代发)
5. 综合结论：法语买家，寻找中国手机配件供应商

【示例10 - 否：内容过短/无实质】
输入：Nice products 👍
判定结果：否
判定依据：
1. 内容有效性：正文过短，无实质采购意图
2. 角色判定：无法判定 - 可能是评论/点赞
3. 关键表达：无采购相关表达
4. 需求匹配：条件A不满足
5. 综合结论：无实质内容，默认判否

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
请分析以下帖子：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{用户原文}"""


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
