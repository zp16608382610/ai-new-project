"""Deterministic synthetic knowledge documents (Phase 3A seed content).

内容为虚构的电商售后政策,与 Phase 2 Mock Business System 的规则保持一致
(退款仅限已签收且全部商品可退、取消仅限 PENDING/PAID/SHIPPED 等)。
不复制任何真实公司政策。所有文本确定性、可重复入库。
"""
from dataclasses import dataclass

from app.db.enums import KnowledgeCategory


@dataclass(frozen=True)
class KnowledgeSpec:
    """One versioned knowledge document to ingest."""

    title: str
    category: KnowledgeCategory
    version: str
    content: str
    language: str = "zh-CN"
    source: str = "internal/mock/policy"
    effective_date: str = "2026-09-01"
    activate: bool = True


REFUND_V1 = """# 退款政策
## 适用范围
本政策适用于在本商城购买商品后,商品已签收且用户提出退款申请的订单。退款申请不代表资金立即退回,资金处理需要经过审核。

## 退款资格
订单状态为已签收(DELIVERED)且订单内全部商品均可退(returnable)时,用户才可以发起退款。仍在运输中、未支付、已取消的订单不可发起退款。

## 退款时效
自商品签收之日起十五个自然日内,用户可以发起退款申请。超过十五天视为超出无理由退款时效,特殊情况可联系客服人工处理。

## 不可退款情形
已取消订单、已退款完成的订单、含不可退商品(如电子产品、保健品等标注不可退类目)的订单,以及超出退款时效的订单均不可退款。

## 退款金额
可退款订单的退款金额等于该订单实付总额,以系统权威数据为准,不支持用户自行指定金额。运费是否退回以实际规则为准。

## 退款处理时效
退款申请提交后进入待审核状态;审核通过后资金通常在三个工作日内原路退回。审核不通过时会说明原因,用户可补充材料后重新申请。

## 特殊情形
商品在运输中破损或签收时发现漏发,可优先联系客服登记异常,由人工介入判定,不受常规退款时效限制。
"""

REFUND_V2 = """# 退款政策
## 适用范围
本政策适用于商品已签收后申请退款的订单。退款请求进入审核流程,不代表资金已退回。

## 退款资格
仅当订单处于已签收(DELIVERED)状态、订单内全部商品均可退,并且该订单当前没有在途退款申请时,才允许发起新的退款。资格判定由业务系统完成。

## 退款时效
签收之日起十五个自然日内可发起退款。签收日期以物流系统中最新签收记录的更新时间为准。

## 不可退款情形
以下情形不可退款:订单已取消;订单已退款完成;订单包含不可退商品;订单处于未支付、运输中状态;订单存在待审核或在途退款;超出十五天退款时效且无特殊审批。

## 退款金额
退款金额由订单的权威数据推导,等于订单实付总额。系统不接受用户或客服手工填写任意金额;若发生部分退款,需通过专项审批流程。

## 退款处理时效
退款申请进入待审核(PENDING)后,平台在一个工作日内完成规则审核。审核通过后进入退款执行阶段,资金原路退回通常需要一到三个工作日。

## 特殊情形
运输破损、漏发、错发等异常订单不受常规时效限制,由客服登记工单后进入人工处理流程。若退款被拒绝,用户可查看拒绝原因并申请重新评估。
"""

RETURN_POLICY = """# 退货政策
## 退货资格
商品支持七天无理由退货的前提是商品类目可退且保持完好。不可退类目包括已拆封的电子产品与保健类商品。

## 退货时效
自签收之日起七天内申请退货,超过时效需提供质量问题证明。

## 退货条件
退货商品需保持原有包装、配件齐全、不影响二次销售。定制类商品不支持退货。

## 退货流程
用户提交退货申请后,客服确认资格并给出退货地址;用户寄回后仓库在三个工作日内完成质检,质检通过即进入退款环节。

## 运费规则
无理由退货的寄回运费由用户承担;质量问题退货的运费由商家承担并随退款一并返还。
"""

EXCHANGE_POLICY = """# 换货政策
## 换货资格
签收后十五天内,商品存在质量问题或与描述不符时支持换货;无理由换货仅在商品类目可退可换且不影响二次销售时支持。

## 换货时效
质量问题换货申请应在签收后十五天内提出,以物流签收记录为准。

## 库存说明
换货需要目标商品有库存;缺货时可选择等价换货、退款或等待补货,客服需明确告知预计补货时间。

## 换货流程
用户提交换货申请,客服登记工单并审核;审核通过后安排寄回,仓库质检后发出替换商品。全程保持工单进度可查。

## 运费规则
质量问题换货双向运费由商家承担;无理由换货寄回运费由用户承担。
"""

LOGISTICS_POLICY = """# 物流与配送政策
## 发货时效
现货订单支付成功后四十八小时内发出,预售商品以页面标注时间为准。

## 配送时效
同城配送一到两天,省内两到三天,跨省三到五天。偏远地区顺延一到两天,恶劣天气或疫情等不可抗力除外。

## 物流状态说明
物流状态包括待揽收、运输中、派送中、已签收与异常。用户可在订单物流页查看最新状态与预计送达时间。

## 异常件处理
物流超过预计时间未更新、显示异常或超过承诺时效未送达时,客服应登记物流异常工单,联系承运方核实并告知用户处理时限。

## 签收确认
包裹签收以承运方系统记录为准;用户声称未收到但系统显示已签收时,客服应核实签收凭证并升级人工处理。
"""

COUPON_POLICY = """# 优惠券政策
## 优惠券类型
优惠券分为店铺券、平台券与专项券;不同券的适用范围与门槛以券面说明为准。

## 使用条件
优惠券需在有效期内使用,且订单金额满足券面门槛。已取消订单占用的优惠券会自动退回。

## 有效期
优惠券有效期以发放时间为起点计算,过期不补发。专项活动券的有效期以活动规则为准。

## 退款退回规则
订单整单退款成功后,满足退回条件的优惠券退回用户账户;部分退款时优惠券不退回,按实际支付金额分摊。

## 异常处理
优惠券未到账、误扣或退回失败时,客服登记工单并核实发放记录,一般在一个工作日内处理完成。
"""

SOP_DOC = """# 客服服务标准作业程序
## 服务边界
客服负责订单查询、物流查询、退款申请受理、取消订单申请与投诉登记;不负责资金审批与人工审核结论。

## 订单查询 SOP
核实用户身份后,使用订单查询能力读取订单;回答需包含订单状态、金额与商品明细;涉及他人订单时拒绝提供并提示。

## 物流查询 SOP
订单查询到最新物流记录后如实告知承运方、运单号、状态与预计送达时间;物流异常时登记工单并给出处理时限。

## 退款 SOP
客服应依据退款资格判定结果回复用户:符合时引导提交退款申请,不符合时说明规则原因;不允许客服绕开规则承诺退款金额。

## 投诉升级 SOP
涉及资金安全、人身安全、反复投诉或用户明确要求转人工时,客服应完整交接对话上下文与业务记录后升级人工处理。

## 交接记录
人工交接必须包含:用户标识、订单号(如有)、问题摘要、已执行操作与待办事项;记录应完整可审计。
"""


def knowledge_specs() -> list[KnowledgeSpec]:
    """Deterministic ordered list of documents to ingest (退款含 v1/v2 版本示例)."""
    return [
        KnowledgeSpec(
            title="退款政策",
            category=KnowledgeCategory.REFUND,
            version="1.0.0",
            content=REFUND_V1,
            effective_date="2026-08-01",
            activate=False,
        ),
        KnowledgeSpec(
            title="退款政策",
            category=KnowledgeCategory.REFUND,
            version="2.0.0",
            content=REFUND_V2,
            effective_date="2026-09-01",
            activate=True,
        ),
        KnowledgeSpec(
            title="退货政策",
            category=KnowledgeCategory.RETURN,
            version="1.0.0",
            content=RETURN_POLICY,
            effective_date="2026-08-15",
        ),
        KnowledgeSpec(
            title="换货政策",
            category=KnowledgeCategory.EXCHANGE,
            version="1.0.0",
            content=EXCHANGE_POLICY,
            effective_date="2026-08-15",
        ),
        KnowledgeSpec(
            title="物流与配送政策",
            category=KnowledgeCategory.LOGISTICS,
            version="1.0.0",
            content=LOGISTICS_POLICY,
            effective_date="2026-08-15",
        ),
        KnowledgeSpec(
            title="优惠券政策",
            category=KnowledgeCategory.COUPON,
            version="1.0.0",
            content=COUPON_POLICY,
            effective_date="2026-08-15",
        ),
        KnowledgeSpec(
            title="客服服务标准作业程序",
            category=KnowledgeCategory.SOP,
            version="1.0.0",
            content=SOP_DOC,
            effective_date="2026-08-20",
        ),
    ]