import os
os.chdir('D:\\workspace\\PRTS')

skills = {}
with open('skills_parsed.txt', 'r', encoding='utf-8') as f:
    for line in f:
        parts = line.strip().split('|')
        if len(parts) >= 5:
            name = parts[0].strip()
            if name not in skills:
                skills[name] = []
            skills[name].append((parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()))

all_ops = {}
with open('干员练度表.txt', 'r', encoding='utf-8') as f:
    for line in f.readlines()[1:]:
        parts = line.strip().split('\t')
        if len(parts) >= 5:
            name = parts[0].strip()
            all_ops[name] = {
                'star': parts[2].strip(),
                'level': parts[3].strip(),
                'elite': parts[4].strip(),
                'owned': parts[1].strip().upper() == 'TRUE'
            }

with open('基建技能一览_全干员.txt', 'w', encoding='utf-8') as f:
    f.write('基建技能一览（全干员）\n')
    f.write('共 ' + str(len(all_ops)) + ' 名干员\n')
    f.write('=' * 80 + '\n\n')
    for name in sorted(all_ops.keys()):
        info = all_ops[name]
        tag = '[已招募]' if info['owned'] else '[未招募]'
        star = info['star']
        level = info['level']
        elite = info['elite'] if info['elite'] else '0'
        f.write(tag + ' 【' + name + '】星级' + star + ' Lv' + level + ' E' + elite + '\n')
        if name in skills:
            for sk in skills[name]:
                f.write('  ' + sk[0] + ' | ' + sk[1] + ' | ' + sk[2] + ' | ' + sk[3] + '\n')
        else:
            f.write('  (无基建技能数据)\n')
        f.write('\n')

print('Total operators: ' + str(len(all_ops)))
print('Operators with skills: ' + str(len([n for n in all_ops if n in skills])))
print('Saved: 基建技能一览_全干员.txt')
