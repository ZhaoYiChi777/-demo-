"""Versioned feasibility-stage interpretations; explicit user edits retain precedence."""
from copy import deepcopy
from atomic_contracts import FIXED, GUIDANCE

CONTRACTS = {
    'TUR-NEC-004': {
        'name': '本厂改造必要性的证据依据',
        'standard': '本厂有明确缺陷、检测异常或本厂预防性风险评估支撑改造必要性。仅引用同型机案例且明确本厂无明显缺陷、无本厂风险论证时不符合。',
        'criteria': ['本厂缺陷、检测异常或本厂预防性风险评估支撑改造必要性'],
        'terms': ['本厂','故障','缺陷','检修','硬度','风险'],
        'suggestion': '请补充本厂缺陷、检测数据或预防性风险评估依据；已有本厂故障证据时不得以问题前提不成立判不符合。',
        'content': '本厂已有故障或检测异常是符合的正向证据，不得以“无明显缺陷”这一问题前提不成立判不符合；未检索到本厂证据为待确认。',
    },
    'TUR-DOC-003': {
        'name': '投资估算章节与总投资',
        'standard': '提供投资估算章节，并填写工程总投资。这里只检查章节及总投资，费用分项是否完整或合理由其他规则检查。',
        'criteria': ['提供投资估算章节', '填写工程总投资'],
        'terms': ['投资估算','工程总投资','总投资'],
        'suggestion': '请提供投资估算章节和工程总投资。费用分项问题另按对应规则核验。',
        'content': '章节存在且工程总投资已填写时符合本条。设计费、调试费等分项填“无”不构成本条不符合，不得扩展为报价或费用组成要求。',
    },
    'TUR-CON-003': {
        'standard': '设备数量、机组编号、项目对象及单位在章节之间不存在实质矛盾。简称、等价名称及可由文字列举算出的数量均可接受。',
        'criteria': ['跨章节项目对象及数量不存在可核验的实质矛盾'],
        'content': '机与机组等简称不构成矛盾；逐段列举的设备可直接计数。缺少对照表不能证明前后不一致。',
    },
    'TUR-SCP-001': {
        'standard': '多机组项目需能从正文识别各机组的改造对象和范围；集成设备可合并列于材料表，不强制重复逐机列明非关键接口。',
        'criteria': ['能够识别各机组的改造对象和范围'],
        'content': '接受正文按机组列举设备、清单合并列数量的方式；只有无法识别改造范围或存在数量矛盾时才影响结论。',
    },
    'TUR-SCP-002': {
        'standard': '主要设备材料与推荐方案实质对应。成套系统可按整套设备列示，清洗、检测、调整等作业不要求分别作为采购设备列项。',
        'criteria': ['主要采购设备或成套系统与推荐方案实质对应'],
        'content': '非关键辅材明细及成套系统内部零件可在设计采购阶段完善；不得把作业步骤当成漏列的设备。核心采购设备确实遗漏时仍需核验。',
    },
    'TUR-SCP-005': {
        'standard': '关键设备的型号、结构或性能指标能说明选型与改造目标的匹配。直接影响安全的承压等级、材料适用性或保护参数不得省略；非关键接口尺寸可后续设计完善。',
        'criteria': ['设备选型或结构性能有依据，未遗漏本次改动直接涉及的关键安全参数'],
        'content': '局部控制装置优化不等于更换全部阀门或承压边界，不得自动要求未改动设备的全部规格表。',
    },
}

STAGE_GUIDANCE = ('先核对原始适用项目类型、系统范围及applicability_condition，再逐项核验固定criteria。'
                  '问题级别和总体结论影响由原规则决定，不得因硬规则一律判否决，不得新增条件。'
                  'support为可选支撑途径，不能要求每个项目具备所有支撑途径；veto仅在明确触发否决事实时不符合。'
                  '缺少必需证据保留待确认，不等于已触发否决；原始问题意见不是本报告事实。')


def contracted(rule):
    rule = deepcopy(rule)
    # Explicit user-edited rules keep their own meaning.
    if rule.get('origin', 'default') == 'default' and not rule['id'].startswith('DEMO-'):
        if rule['id'] in CONTRACTS:
            rule.setdefault('original_standard', rule.get('standard', ''))
            rule.update(CONTRACTS[rule['id']])
        if rule['id'] in FIXED:
            rule.update(FIXED[rule['id']])
            rule['standard'] = '；'.join(rule['criteria'])
            rule['content'] = '适用条件：' + rule['applicability_condition'] + '。按固定判据核验，原始触发条件与等级见source_metadata。'
            clarification = CONTRACTS.get(rule['id'], {}).get('content', '') + GUIDANCE.get(rule['id'], '')
            rule.update(contract_version=4, review_guidance=STAGE_GUIDANCE + clarification)
            rule['version'] = max(6,rule.get('version',1))
    return rule


def excluded(check):
    if check.get('reviewed_by'):
        return check['result'] == 'not_applicable'
    decision = check.get('judgment', {})
    return check['result'] == 'not_applicable' or (
        decision.get('status') == 'completed' and decision.get('applicability') == 'not_applicable')
