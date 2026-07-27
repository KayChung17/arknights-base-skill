"""
efficiency_calculator.py — 明日方舟基建效率计算器 (v4)
支持：分层计算、乘算体系(但书×1.556)、赤金生产线全局计数、全局干员统计
"""

import os, re, json, sys
from collections import defaultdict, Counter

os.chdir('D:\\workspace\\PRTS')

# ===== 干员标签表 =====
TAGS = {
    '杜林族': {'桃金娘', '杜林', '褐果'},
    '莱茵生命': {'伊芙利特', '赫默', '白面鸮', '梅尔', '麦哲伦', '多萝西', '娜斯提', '缪尔赛思', '淬羽赫默', '星源'},
    '深海猎人': {'歌蕾蒂娅', '斯卡蒂', '浊心斯卡蒂', '幽灵鲨', '归溟幽灵鲨', '安哲拉', '乌尔比安'},
    '格拉斯哥帮': {'推进之王', '摩根', '达格达', '因陀罗', '戴菲恩'},
    '叙拉古': {'德克萨斯', '拉普兰德', '伺夜', '贝洛内', '斥罪'},
    '岁': {'夕', '令', '年', '重岳', '黍', '余', '烛煌'},
    '精英干员': {'凯尔希', '阿米娅', '煌', '迷迭香', '凯尔希·思衡托', 'Logos', '阿斯卡纶'},
    '金属工艺': {'苍苔', '引星棘刺', '砾', '斑点', '夜烟', '温米'},
    '莱茵科技': {'多萝西', '娜斯提', '白面鸮', '赫默', '星源', '淬羽赫默'},
    '红松骑士团': {'野鬃', '灰毫'},
    '作业平台': {'THRM-EX', '正义骑士号', 'Friston-3', 'Lancet-2', 'Castle-3'},
}

# ===== 数据加载 =====
def load_skills():
    skills = defaultdict(list)
    with open('skills_parsed.txt', 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 5:
                skills[parts[0].strip()].append({
                    'elite': parts[1].strip(),
                    'facility': parts[2].strip(),
                    'skill_name': parts[3].strip(),
                    'desc': parts[4].strip()
                })
    return dict(skills)

def load_roster():
    roster = {}
    with open('干员练度表.txt', 'r', encoding='utf-8') as f:
        for line in f.readlines()[1:]:
            parts = line.strip().split('\t')
            if len(parts) >= 5 and parts[1].strip().upper() == 'TRUE':
                roster[parts[0].strip()] = {
                    'star': parts[2].strip(),
                    'level': parts[3].strip(),
                    'elite': parts[4].strip() if parts[4].strip() else '0'
                }
    return roster

def skill_unlocked(skill_elite, roster_elite):
    if skill_elite in ('无', '30级', 'Lv'):
        return True
    m = re.match(r'精(\d)', skill_elite)
    if m:
        return int(roster_elite) >= int(m.group(1)) if roster_elite.isdigit() else False
    return True

def get_operator_tags(name):
    tags = []
    for tag, members in TAGS.items():
        if name in members:
            tags.append(tag)
    return tags

# ===== 全局环境 =====
# 设计排班前先设定这些参数
class BaseEnvironment:
    def __init__(self, trade_count=3, power_count=2, dorm_level=5, meeting_level=5, mode='default'):
        self.trade_count = trade_count
        self.power_count = power_count
        self.dorm_level = dorm_level
        self.meeting_level = meeting_level
        self.mode = mode  # 'default' or 'production_line'
        # 以下由全局计数器填充
        self.dulin_deployed = 0
        self.rhine_deployed = 0
        self.elite_deployed = 0
        self.deepsea_deployed = 0
        self.glasgow_deployed = 0
        self.syracuse_deployed = 0
        self.actual_gold_lines = 0  # 实际赤金制造站数
        self.room_operators = {}    # 当前班次所有房间的干员分配

    def set_global_counters(self, all_room_assignments):
        """从全基建的干员分配统计全局计数器"""
        deployed = set()
        self.actual_gold_lines = 0
        room_counts = defaultdict(int)
        for room_type, ops, product in all_room_assignments:
            for op in ops:
                deployed.add(op)
            if room_type == '制造站' and product in ('贵金属', '赤金', 'Pure Gold'):
                self.actual_gold_lines += 1
            room_counts[room_type] += 1
        self.trade_count = room_counts.get('贸易站', self.trade_count)

        counters = Counter()
        for op in deployed:
            for tag in get_operator_tags(op):
                counters[tag] += 1
        self.dulin_deployed = counters.get('杜林族', 0)
        self.rhine_deployed = counters.get('莱茵生命', 0)
        self.elite_deployed = counters.get('精英干员', 0)
        self.deepsea_deployed = counters.get('深海猎人', 0)
        self.glasgow_deployed = counters.get('格拉斯哥帮', 0)
        self.syracuse_deployed = counters.get('叙拉古', 0)

    def get_production_lines(self):
        """计算当前赤金生产线总数（含虚拟）"""
        actual = self.actual_gold_lines
        dulin_virtual = min(self.dulin_deployed, 4)  # 鸿雪最多4
        # 默认模式：绮良翻倍仅基于实际（保守算法）
        qiliang_double = actual  # 每2条实际+2条虚拟，相当于实际条数直接翻倍
        total = actual + dulin_virtual + qiliang_double
        return total, actual, dulin_virtual, qiliang_double


# ===== 分层计算引擎 =====

def calc_room_v4(operators, room_type, product_type, env):
    """
    分层计算单个房间效率。
    返回: { 'paper': 纸面效率, 'layers': {...}, 'equiv': 等效说明 }
    """
    skills_data = load_skills()
    roster = load_roster()

    print(f'═' * 60)
    print(f'{room_type} — {", ".join(operators)} [{product_type if product_type else ""}]')
    print(f'═' * 60)

    # ===== 分层收集 =====
    layer_operator = 0   # 干员纸面（相互叠加/清空）
    layer_facility = 0   # 设施数量加成（按贸易站/发电站）
    layer_global = 0     # 全局计数器加成
    layer_multipliers = []  # 乘算区（但书）
    layer_independent = [] # 独立收益（龙舌兰+500等）
    warehouse = 0
    locked = []
    mismatched = []
    equiv_notes = []

    # 标记是否有巫恋低语
    has_wulian_low = False
    wulian_value = 0  # 巫恋低语下每人自身+45%

    for op_raw in operators:
        op = op_raw.strip()
        if op not in skills_data:
            continue
        info = roster.get(op, {})
        roster_elite = info.get('elite', '0')

        for sk in skills_data[op]:
            if sk['facility'] != room_type:
                continue
            desc = sk['desc']
            elite = sk['elite']

            if not skill_unlocked(elite, roster_elite):
                needed = re.match(r'精(\d)', elite)
                n = int(needed.group(1)) if needed else 0
                locked.append(f'{op}:{sk["skill_name"]} | 需E{n}但只有E{roster_elite}')
                continue

            # 配方检查
            if room_type == '制造站' and product_type:
                is_record_only = '作战记录类配方' in desc and '生产力' in desc
                is_origin_only = '源石类配方' in desc and '生产力' in desc
                product_is_gold = product_type in ('贵金属', '赤金', 'Pure Gold')
                if is_record_only and not product_type in ('作战记录', 'Battle Record'):
                    mismatched.append(f'{op}:{sk["skill_name"]} 限作战记录')
                    continue
                if is_origin_only and not product_type in ('源石碎片', 'Origin Stone'):
                    mismatched.append(f'{op}:{sk["skill_name"]} 限源石')
                    continue

            # =============================
            # 第一层：干员纸面效率（互相叠加）
            # =============================
            op_face = 0

            m = re.search(r'订单获取效率\+(\d+)%', desc)
            if m: op_face += int(m.group(1))
            m = re.search(r'生产力\+(\d+)%', desc)
            if m: op_face += int(m.group(1))
            m = re.search(r'生产力[-－](\d+)%', desc)
            if m: op_face -= int(m.group(1))

            # 吉星/风丸——除自身外每名干员+X%
            m = re.search(r'除自身以外每名处于工作状态的干员\+(\d+)%', desc)
            if m: op_face += int(m.group(1)) * 2

            # 订单上限
            limit = 0
            m = re.search(r'订单上限\+(\d+)', desc)
            if m: limit += int(m.group(1))
            m = re.search(r'订单上限[-－](\d+)', desc)
            if m: limit -= int(m.group(1))
            if '当前贸易站每级+1个订单上限' in desc: limit += 3

            # 标准化/基础技能
            m = re.search(r'标准化·[αβ]', desc)
            if m and not re.search(r'(作战记录|贵金属|源石)', desc):
                pass  # 已经在生产力匹配中被计入

            # 阿罗玛渐增
            if '每小时+2%' in desc and '最终达到+20%' in desc:
                equiv_notes.append(f'{op}:阿罗玛渐增(8h后=+20%,平均约+17%)')
                op_face += 17

            # 克洛丝慢性子/芬急性子
            if '首小时+20%' in desc and '最终达到+25%' in desc:
                equiv_notes.append(f'{op}:渐增(首20%渐至25%,平均≈+23%)')
                op_face += 23
            if '首小时+15%' in desc and '最终达到+25%' in desc:
                equiv_notes.append(f'{op}:渐增(首15%渐至25%,平均≈+22%)')
                op_face += 22

            # =============================
            # 巫恋低语——特殊处理
            # =============================
            if '全部归零' in desc and '每人为自身' in desc:
                m = re.search(r'每人为自身\+(\d+)%', desc)
                if m:
                    has_wulian_low = True
                    wulian_value = int(m.group(1))
                    equiv_notes.append(f'{op}:巫恋低语→清空队友纸面,3人各+{wulian_value}%=+{wulian_value*3}%')
                    op_face = 0  # 低语本身不加纸面，它替代其他人

            # =============================
            # 第二层：设施数量加成
            # =============================
            fac_bonus = 0
            # 清流/引星棘刺——每个贸易站为赤金+X%
            m = re.search(r'每个贸易站.*?(贵金属|当前制造站).*?(\d+)%', desc)
            if m: fac_bonus += int(m.group(2)) * env.trade_count

            # 温蒂/森蚺/异客——每个发电站+X%
            m = re.search(r'每个发电站.*?(\d+)%', desc)
            if m: fac_bonus += int(m.group(1)) * env.power_count

            # 空弦——每间宿舍每级+2%
            m = re.search(r'每间宿舍每级\+(\d+)%', desc)
            if m: fac_bonus += int(m.group(1)) * 4 * env.dorm_level

            # 娜仁图亚——每间宿舍每级为赤金+1%
            m = re.search(r'每间宿舍每级.*?贵金属.*?(\d+)%', desc)
            if m: fac_bonus += int(m.group(1)) * 4 * env.dorm_level

            # 伺夜——会客室每级+X%
            m = re.search(r'会客室每级.*?(\d+)%.*?最多(\d+)%', desc)
            if m: fac_bonus += min(env.meeting_level * int(m.group(1)), int(m.group(2)))

            # 冬时——按人数（满员30%）
            if '每个当前制造站内干员' in desc and '+10%生产力' in desc:
                equiv_notes.append(f'{op}:冬时归零,满员3人=+30%')
                fac_bonus += 30

            # =============================
            # 第三层：全局计数器加成
            # =============================
            gl_bonus = 0
            # 鸿雪——赤金生产线（每生产线+5%）
            if '每有1条赤金生产线' in desc:
                total_lines, actual, dv, qd = env.get_production_lines()
                gl_bonus += total_lines * 5
                equiv_notes.append(f'{op}:赤金生产线{total_lines}条(实际{actual}+杜林{dv}+绮良{qd})=+{total_lines*5}%')

            # 绮良——每2条实际生产线+2条虚拟
            if '每有2条赤金生产线' in desc and '额外提供' in desc:
                equiv_notes.append(f'{op}:绮良每2实际→+2虚拟({env.actual_gold_lines}实际=+{env.actual_gold_lines}虚拟)')

            # 图耶——每2条生产线+15%
            m = re.search(r'每有2条赤金生产线.*?(\d+)%', desc)
            if m:
                total_lines, _, _, _ = env.get_production_lines()
                val = (total_lines // 2) * int(m.group(1))
                gl_bonus += val
                equiv_notes.append(f'{op}:图耶{total_lines}条÷2×{m.group(1)}%=+{val}%')

            # 娜斯提——每莱茵+3%
            m = re.search(r'基建内.*?每有1名莱茵生命.*?(\d+)%', desc)
            if m:
                val = min(env.rhine_deployed, 5) * int(m.group(1))
                gl_bonus += val
                equiv_notes.append(f'{op}:莱茵{env.rhine_deployed}名×{m.group(1)}%=+{val}%')

            # 缪尔赛思——每莱茵+3%无人机
            m = re.search(r'每有1名.*?莱茵生命.*?(\d+)%', desc)
            if m and room_type == '发电站':
                val = min(env.rhine_deployed, 5) * int(m.group(1))
                gl_bonus += val
                equiv_notes.append(f'{op}:莱茵{env.rhine_deployed}名×{m.group(1)}%=+{val}%')

            # 苍苔——每金属工艺+5%
            if '每个金属工艺类技能' in desc:
                equiv_notes.append(f'{op}:金属工艺联动(需同站金属工艺干员)')

            # 八幡海铃——每叙拉古+5%
            if '叙拉古' in desc and '订单获取效率' in desc:
                val = env.syracuse_deployed * 5
                gl_bonus += val
                equiv_notes.append(f'{op}:叙拉古{env.syracuse_deployed}名=+{val}%')

            # 戴菲恩——每格拉斯哥帮+10%
            if '格拉斯哥帮' in desc and '订单获取效率' in desc:
                val = env.glasgow_deployed * 10
                gl_bonus += val
                equiv_notes.append(f'{op}:格拉斯哥{env.glasgow_deployed}名=+{val}%')

            # =============================
            # 第四层：乘算区/独立收益
            # =============================
            # 但书——×1.556乘区
            if '视为违约订单' in desc and '交付数' in desc:
                layer_multipliers.append(('但书', 1.556))
                equiv_notes.append('但书乘区:总效率×1.556')
            if '违约索赔' in desc:
                pass  # 已包含在但书乘区内

            # 龙舌兰——独立收益
            m = re.search(r'龙门币收益\+(\d+)', desc)
            if m:
                layer_independent.append(f'大单龙币+{m.group(1)}')

            # 雪雉——放大器（按队友纸面）
            m = re.search(r'每(\d+)%.*?额外.*?(\d+)%.*?最多(\d+)%', desc)
            if m:
                equiv_notes.append(f'{op}:雪雉放大(每{m.group(1)}%额外{m.group(2)}%,最多{m.group(3)}%)')

            # =============================
            # 收集仓库
            # =============================
            m = re.search(r'仓库容量上限\+(\d+)', desc)
            if m: warehouse += int(m.group(1))

            # 红云/泡泡仓库转化
            if '每格仓库容量' in desc and '生产力' in desc:
                equiv_notes.append(f'{op}:仓库转化(每格+2%)')

            # =============================
            # 累加到对应层级
            # =============================
            if has_wulian_low:
                # 巫恋模式下，其他人的纸面被清空
                if not ('全部归零' in desc and '每人为自身' in desc):
                    pass  # 其他人的纸面被巫恋覆盖
                layer_operator = wulian_value * 3  # 低语直接给总固定值
            else:
                layer_operator += op_face

            layer_facility += fac_bonus
            layer_global += gl_bonus

            if op_face or fac_bonus or gl_bonus:
                detail = f'+{op_face}%' if op_face else ''
                if fac_bonus:
                    detail += f' 设施+{fac_bonus}%' if detail else f'设施+{fac_bonus}%'
                if gl_bonus:
                    detail += f' 全局+{gl_bonus}%' if detail else f'全局+{gl_bonus}%'
                if not detail:
                    detail = ''
                print(f'  {op}: {detail}' if detail else f'  {op}:')

    # ===== 最终计算 =====
    paper_total = layer_operator + layer_facility + layer_global
    equiv_total = paper_total

    # 应用乘算
    for name, mult in layer_multipliers:
        equiv_total = equiv_total * mult

    print()
    print(f'干员纸面: {layer_operator:+d}%')
    print(f'设施加成: {layer_facility:+d}%')
    print(f'全局计数: {layer_global:+d}%')
    print(f'纸面合计: {paper_total:+d}%')

    if layer_multipliers:
        mult_str = ' × '.join([f'{m:.3f}' for _, m in layer_multipliers])
        print(f'乘算区: {mult_str} = {equiv_total:.1f}%')
        print(f'等效效率: {equiv_total:.1f}%')

    if layer_independent:
        for s in layer_independent:
            print(f'独立收益: {s}')

    if locked:
        print(f'\n🔒 未解锁:')
        for l in locked[:3]:
            print(f'  {l}')
    if mismatched:
        print(f'\n⚠️ 不匹配:')
        for m in mismatched[:3]:
            print(f'  {m}')
    if equiv_notes:
        print(f'\n特殊机制:')
        for n in equiv_notes[:5]:
            print(f'  {n}')

    return {
        'paper': paper_total,
        'equiv': equiv_total,
        'layers': {'operator': layer_operator, 'facility': layer_facility, 'global': layer_global},
        'multipliers': layer_multipliers,
    }


# ===== 排班校验 =====
def check_schedule_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        schedule = json.load(f)

    print(f'校验: {schedule.get("title", "")}')
    print()

    pmap_trade = {'LMD': '龙门币', 'Synthetic Jade': '合成玉'}
    pmap_manu = {'Pure Gold': '贵金属', 'Battle Record': '作战记录', 'Origin Stone': '源石碎片'}
    rmap = {
        'trading': ('贸易站', pmap_trade),
        'manufacture': ('制造站', pmap_manu),
        'power': ('发电站', None),
        'control': ('控制中枢', None),
        'meeting': ('会客室', None),
        'hire': ('办公室', None),
    }

    all_results = []

    for plan in schedule.get('plans', []):
        print(f'## {plan["name"]}')
        rooms = plan.get('rooms', {})
        env = BaseEnvironment(trade_count=3, power_count=2, dorm_level=5)

        # 第一遍扫描：收集所有干员分配用于全局计数
        all_assignments = []
        for room_key, room_list in rooms.items():
            if room_key not in rmap: continue
            rt, pmap = rmap[room_key]
            for room in room_list:
                ops = room.get('operators', [])
                product_code = room.get('product', '')
                product_name = pmap.get(product_code, product_code) if pmap else ''
                all_assignments.append((rt, ops, product_name))

        env.set_global_counters(all_assignments)
        # 输出全局计数器
        print(f'  全局: 杜林{env.dulin_deployed} 莱茵{env.rhine_deployed} 精英{env.elite_deployed} 深海{env.deepsea_deployed} 叙拉古{env.syracuse_deployed} 赤金线{env.actual_gold_lines}条')
        total_lines, actual, dv, qd = env.get_production_lines()
        print(f'  赤金生产线: {total_lines}条(实际{actual}+杜林{dv}+绮良{qd})')
        print()

        # 第二遍：逐房间计算
        for room_key, room_list in rooms.items():
            if room_key not in rmap: continue
            rt, pmap = rmap[room_key]
            for room_idx, room in enumerate(room_list):
                ops = room.get('operators', [])
                product_code = room.get('product', '')
                product_name = pmap.get(product_code, product_code) if pmap else ''
                print(f'  {rt}#{room_idx+1} [{product_name}]: {", ".join(ops)}')
                result = calc_room_v4(ops, rt, product_name or None, env)
                all_results.append(result)
                print()

        # 干员冲突检查
        plan_ops = set()
        for room_key, room_list in rooms.items():
            if room_key not in rmap: continue
            for room in room_list:
                for op in room.get('operators', []):
                    if op in plan_ops:
                        print(f'  ⚠️ 同班重复: {op}')
                    plan_ops.add(op)

    print('=' * 60)
    print('全局计数器分析')
    print('=' * 60)
    # 统计各标签
    all_deployed = set()
    for plan in schedule.get('plans', []):
        rooms = plan.get('rooms', {})
        for room_key, room_list in rooms.items():
            if room_key not in rmap: continue
            for room in room_list:
                for op in room.get('operators', []):
                    all_deployed.add(op)
    counters = Counter()
    for op in all_deployed:
        for t in get_operator_tags(op):
            counters[t] += 1
    for c in ['杜林族', '莱茵生命', '精英干员', '深海猎人', '格拉斯哥帮', '叙拉古', '金属工艺']:
        if counters[c]:
            print(f'  {c}: {counters[c]}名')


# ===== 主入口 =====
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        fp = sys.argv[2] if len(sys.argv) > 2 else '243 搓玉赚钱三班倒.json'
        check_schedule_file(fp)
    elif len(sys.argv) >= 2:
        # 手动指定组合测试
        room_type = sys.argv[1]
        ops = sys.argv[2].split(',') if len(sys.argv) > 2 else []
        product = sys.argv[3] if len(sys.argv) > 3 else None
        env = BaseEnvironment(trade_count=3, power_count=2)
        env.actual_gold_lines = 4
        env.dulin_deployed = 4
        calc_room_v4(ops, room_type, product, env)
    else:
        print('用法: python efficiency_calculator.py --check 排班文件.json')
