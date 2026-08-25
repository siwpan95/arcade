filepath = r'd:\allin\art\kid_a_walker.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 修复 Line 497: frozenset -> set
old_line497 = "        self._exits[coord]  = exits"
new_line497 = "        self._exits[coord]  = set(exits)"

if old_line497 in content:
    content = content.replace(old_line497, new_line497, 1)
    print("Fixed Line 497: frozenset -> set")
else:
    print("WARNING: Pattern not found for Line 497")

# 修复 Line 499: 移除 frozenset 返回类型注解
old_line499 = "    def _pick_exits(self, forced) -> frozenset:"
new_line499 = "    def _pick_exits(self, forced):"

if old_line499 in content:
    content = content.replace(old_line499, new_line499, 1)
    print("Fixed Line 499: removed frozenset return type")
else:
    print("WARNING: Pattern not found for Line 499")

# 修复 Line 575: set -> set[str] 或移除类型注解
old_line575 = "    def exits_of(self, cx, cy) -> set:"
new_line575 = "    def exits_of(self, cx, cy):"

if old_line575 in content:
    content = content.replace(old_line575, new_line575, 1)
    print("Fixed Line 575: removed set return type")
else:
    print("WARNING: Pattern not found for Line 575")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("\nDone!")
