# 绘图模板库（出版级可视化）

本库提供可直接套用的 matplotlib/seaborn 绘图模板。平台沙箱已注入 `save_figure(name)`
辅助函数：它会同时输出 `/output/{name}.png`（150dpi 展示图）与
`/output/pub/` 下的 300dpi PNG、SVG、PDF 出版资产。所有模板假定已完成
`import matplotlib.pyplot as plt`、`import numpy as np`，颜色请使用下方无障碍调色板。

## 通用出版规范（字号/留白/图例）

- 字号：坐标轴标签 8-9pt，刻度 7-8pt，图例 7-8pt，标题 9-10pt；避免任何文字小于 7pt。
- 画布：单栏图推荐 `figsize=(3.5, 2.6)`，双栏图 `figsize=(7.2, 3.2)`（英寸）。
- 图例不带边框（`frameon=False`），置于图内空白处或图顶部，避免遮挡数据。
- 去掉顶部和右侧边框线（`ax.spines[['top','right']].set_visible(False)`），网格线浅灰
  `ax.grid(alpha=0.25, linewidth=0.6)` 且置于数据下层。
- 每张图必须有：自变量轴标签（含单位）、因变量轴标签（含单位）、必要时样本量 n。
- 配色优先使用无障碍调色板：Okabe-Ito 八色
  `#000000 #E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D46A6A #CC79A7`（避免红绿对比）。
- 导出前调用 `plt.tight_layout()`；不要依赖默认配色（`C0/C1`）做正负对比。

## 分组柱状图 + 误差线 + 显著性标注

适用：比较 2-4 组在类别维度上的均值差异，并标注统计显著性。

```python
import matplotlib.pyplot as plt
import numpy as np
colors = ['#0072B2', '#E69F00']
labels = ['对照组', '处理组']
means = [df_g.mean().tolist() ...]  # 用真实统计量替换
errors = [df_g.std(ddof=1).tolist() ...]
x = np.arange(len(categories)); width = 0.35
fig, ax = plt.subplots(figsize=(5.2, 3.6))
for i, (m, e, lb) in enumerate(zip(means, errors, labels)):
    ax.bar(x + (i - 0.5) * width, m, width, yerr=e, capsize=3,
           label=f'{lb} (n={n[i]})', color=colors[i], edgecolor='black', linewidth=0.6,
           error_kw={'elinewidth': 0.8, 'ecolor': '#444444'})
# 显著性标注：对 p<0.05 的组对画横线与星号
def annotate_sig(ax, x1, x2, y, stars='*'):
    ax.plot([x1, x1, x2, x2], [y, y*1.02, y*1.02, y], lw=0.8, color='#333333')
    ax.text((x1 + x2) / 2, y*1.03, stars, ha='center', va='bottom', fontsize=9)
ax.set_xticks(x); ax.set_xticklabels(categories)
ax.set_ylabel('产率 (yield %)')  # 轴标签必须带单位
ax.legend(frameon=False, fontsize=8)
ax.spines[['top', 'right']].set_visible(False)
plt.tight_layout(); save_figure('fig1_grouped_bar')
```

要点：误差线必须注明是 SD 还是 SEM 还是 95% CI（在图注文字中说明）；显著性星号
`* p<0.05, ** p<0.01, *** p<0.001`；组间比较超过 2 组时先做多重比较校正。

## 箱线图 + 异常点标注

适用：分布比较、离群观察；比柱状图更抗异常值误导。

```python
fig, ax = plt.subplots(figsize=(4.8, 3.4))
bp = ax.boxplot([group_a, group_b], labels=['对照组', '处理组'], patch_artist=True,
                widths=0.5, flierprops=dict(marker='o', markersize=3,
                markerfacecolor='none', markeredgecolor='#D55E00'))
for patch, color in zip(bp['boxes'], ['#56B4E9', '#E69F00']):
    patch.set_facecolor(color); patch.set_alpha(0.75)
# 异常点标注：IQR 1.5 倍规则
q1, q3 = np.percentile(group_a, [25, 75]); iqr = q3 - q1
for v in group_a[(group_a < q1 - 1.5*iqr) | (group_a > q3 + 1.5*iqr)]:
    ax.annotate(f'{v:.1f}', (1, v), textcoords='offset points', xytext=(12, 0),
                fontsize=7, color='#D55E00')
ax.set_ylabel('纯度 (%)'); ax.spines[['top', 'right']].set_visible(False)
plt.tight_layout(); save_figure('fig2_box')
```

## 散点图 + 回归线 + 置信带

适用：两连续变量的关系与趋势，报告 Pearson r 与拟合方程。

```python
from scipy import stats
fig, ax = plt.subplots(figsize=(4.8, 3.6))
ax.scatter(x, y, s=22, alpha=0.75, color='#0072B2', edgecolor='white', linewidth=0.4)
slope, intercept, r, p, se = stats.linregress(x, y)
xs = np.linspace(x.min(), x.max(), 100)
ax.plot(xs, intercept + slope * xs, color='#D55E00', lw=1.2,
        label=f'y = {slope:.2f}x + {intercept:.2f}\n$R^2$ = {r**2:.3f}, p = {p:.1e}')
ax.set_xlabel('温度 (°C)'); ax.set_ylabel('产率 (%)')
ax.legend(frameon=False, fontsize=8, loc='lower right')
plt.tight_layout(); save_figure('fig3_scatter')
```

要点：先检查线性与异常值；p 值用科学计数法；如需置信带用 `seaborn.regplot(ci=95)`。

## 相关性热力图

适用：多变量相关结构概览；矩阵上标注 r 值，色标注明范围。

```python
import seaborn as sns
corr = df[numeric_cols].corr(method='pearson')  # 偏态数据改用 spearman
fig, ax = plt.subplots(figsize=(5.6, 4.6))
sns.heatmap(corr, annot=True, fmt='.2f', cmap='RdBu_r', vmin=-1, vmax=1,
            square=True, linewidths=0.5, annot_kws={'fontsize': 7}, ax=ax)
ax.set_title('Pearson 相关系数矩阵', fontsize=9)
plt.tight_layout(); save_figure('fig4_heatmap')
```

要点：r∈[-1,1]；|r|>0.7 视为强相关；样本量小时在文字中补报 p 值；绿色/红色对比
对色盲不友好，统一用 RdBu_r 双向色带。

## 时间序列 / 折线趋势图

适用：随实验轮次、时间、剂量等有序变量的趋势；多条曲线需区分线型。

```python
fig, ax = plt.subplots(figsize=(6.0, 3.2))
ax.plot(x, y, marker='o', markersize=4, lw=1.4, color='#0072B2', label='实验组')
ax.plot(x, y_base, marker='s', markersize=4, lw=1.4, linestyle='--',
        color='#E69F00', label='对照组')
ax.fill_between(x, y - err, y + err, color='#0072B2', alpha=0.15, linewidth=0)
ax.set_xlabel('反应时间 (min)'); ax.set_ylabel('选择性 (%)')
ax.legend(frameon=False, fontsize=8)
plt.tight_layout(); save_figure('fig5_trend')
```

## 直方图 / 密度图（分布检查）

适用：单变量分布、正态性初判；必须先看分布再做参数检验。

```python
fig, ax = plt.subplots(figsize=(4.6, 3.2))
ax.hist(values, bins='auto', color='#56B4E9', edgecolor='white', linewidth=0.5,
        density=True, alpha=0.85, label=f'n = {len(values)}')
sns.kdeplot(values, ax=ax, color='#D55E00', lw=1.2)
ax.axvline(np.mean(values), color='#333333', lw=1, linestyle=':', label=f'均值 {np.mean(values):.2f}')
ax.axvline(np.median(values), color='#009E73', lw=1, linestyle='--', label=f'中位数 {np.median(values):.2f}')
ax.set_xlabel('粒径 (nm)'); ax.set_ylabel('概率密度')
ax.legend(frameon=False, fontsize=7.5)
plt.tight_layout(); save_figure('fig6_hist')
```

要点：均值与中位数明显分离提示偏态；偏态数据优先报告中位数+IQR。

## 小提琴图 / 雨云图

适用：同时展示分布形状与组间比较，样本量 ≥20 时优于箱线图。

```python
fig, ax = plt.subplots(figsize=(5.0, 3.4))
parts = ax.violinplot([g1, g2, g3], showmeans=True, showextrema=False)
for pc, color in zip(parts['bodies'], ['#56B4E9', '#E69F00', '#009E73']):
    pc.set_facecolor(color); pc.set_alpha(0.7); pc.set_edgecolor('black')
ax.set_xticks([1, 2, 3]); ax.set_xticklabels(['A 组', 'B 组', 'C 组'])
ax.set_ylabel('转化率 (%)'); plt.tight_layout(); save_figure('fig7_violin')
```

## 多面板组合图（figure grid）

适用：一个主题的多子图拼版；子图用 (a)(b)(c) 字母标注，共享轴标签避免冗余。

```python
fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), sharey=True)
for ax, (data, title) in zip(axes, [(left, '(a) 温度'), (right, '(b) pH')]):
    ax.scatter(data[0], data[1], s=18, color='#0072B2')
    ax.set_title(title, fontsize=9); ax.set_xlabel('变量值')
axes[0].set_ylabel('产率 (%)')
plt.tight_layout(); save_figure('fig8_panels')
```

## 类别占比 / 堆积条形（替代饼图）

适用：展示构成比例；期刊普遍不推荐饼图，用 100% 堆积条形替代。

```python
fig, ax = plt.subplots(figsize=(5.4, 2.8))
bottoms = np.zeros(len(categories))
for value, color, label in zip(shares, ['#0072B2', '#E69F00', '#009E73'], ['合格', '待复检', '不合格']):
    ax.barh(categories, value, left=bottoms, color=color, label=label,
            edgecolor='white', linewidth=0.6)
    bottoms += value
ax.set_xlabel('占比 (%)'); ax.set_xlim(0, 100)
ax.legend(frameon=False, ncol=3, fontsize=8, loc='upper center', bbox_to_anchor=(0.5, 1.18))
plt.tight_layout(); save_figure('fig9_stacked')
```

## 回归诊断四联图（残差检查）

适用：线性回归报告前必须检查假设；残差应无明显模式、QQ 图应贴近直线。

```python
import statsmodels.api as sm  # 沙箱未装时可手绘残差 vs 拟合值
fig, axes = plt.subplots(2, 2, figsize=(7.0, 5.6))
axes[0, 0].scatter(fitted, residuals, s=16, color='#0072B2')
axes[0, 0].axhline(0, color='#D55E00', lw=1, linestyle='--')
axes[0, 0].set_xlabel('拟合值'); axes[0, 0].set_ylabel('残差')
# QQ 图：scipy.stats.probplot(residuals, plot=axes[0, 1])
# 直方图 / 位置尺度图同理；逐子图收紧留白
plt.tight_layout(); save_figure('fig10_diagnostics')
```
