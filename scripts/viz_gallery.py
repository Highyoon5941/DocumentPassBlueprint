"""시각화 후보 미리보기 — 기준(개념) = 차원, 문서 = 점, 정답 경계 = 선형 경계면.

파이프라인 산출물(work/…)만 읽어 후보 그림 8장을 PNG 로 만든다. **LLM 을 호출하지 않는다.**
어느 방식을 리포트에 넣을지 고르기 위한 도구다 (파이프라인에는 아직 통합하지 않았다).

모델: 평가자 수준 계층 로지스틱 (공통 가중치 w + 평가자별 절편 β_r) — 기준(개념) 열만 쓴다.
  문서 점수        s = w·x
  평가자 r 의 경계  s = −(b + β_r)                 (평가자마다 평행하게 이동한 경계면)
  정답 경계        Π_r σ(s + b + β_r) = 0.5        (평가자 전원 Pass 확률 50%)

사용:
  python scripts/viz_gallery.py --config <cfg> [--group G] [--out docs/img/viz]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

# ---------------------------------------------------------------- 색·모양 (dataviz 기준 팔레트)
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
PASS, MIXED, FAIL = "#2a78d6", "#898781", "#e34948"       # 발산 쌍 파랑↔빨강 + 중립 회색
PRESENT, ABSENT = "#52514e", "#f0efec"                    # 기준 있음/없음 (Pass/Fail 색과 겹치지 않게 무채색)
CLASSES = {"전원 Pass": (PASS, "o"), "일부 Fail": (MIXED, "D"), "전원 Fail": (FAIL, "X")}


def _style() -> None:
    from svmtrial.report import _font_setup

    _font_setup()
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.6, "grid.linestyle": "-", "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 9.5, "axes.titlesize": 12, "axes.titleweight": "bold", "legend.frameon": False,
    })


def _frame(fig, title: str, sub: str, top: float = 0.80) -> None:
    """제목·부제는 그림 위 고정 영역에. 데이터 영역과 겹치지 않는다."""
    fig.text(0.012, 0.975, title, fontsize=12.5, fontweight="bold", color=INK, va="top")
    fig.text(0.012, 0.928, sub, fontsize=8.5, color=INK2, va="top")
    fig.subplots_adjust(top=top, left=0.085, right=0.975, bottom=0.11)


def _scatter(ax, x, y, cls, **kw) -> None:
    for name, (color, marker) in CLASSES.items():
        m = cls == name
        if m.any():
            ax.scatter(x[m], y[m], s=46, c=color, marker=marker, edgecolors=SURFACE, linewidths=1.1,
                       label=f"{name} ({int(m.sum())})", zorder=3, **kw)


def _legend(ax, extra: list | None = None) -> None:
    """범례는 축 바로 위 한 줄 — 점을 가리지 않는다."""
    h, lab = ax.get_legend_handles_labels()
    ax.legend(h + [e[0] for e in (extra or [])], lab + [e[1] for e in (extra or [])], loc="lower left",
              bbox_to_anchor=(0.0, 1.005), ncol=6, fontsize=8.3, labelcolor=INK2, handletextpad=0.4,
              columnspacing=1.2, borderaxespad=0.0)


def _rater_groups(W) -> list[tuple[float, str]]:
    """경계값이 같은 평가자는 한 라벨로 합친다 (예: Person1=Person2). 관대한(왼쪽) 순."""
    groups: dict[float, list[str]] = {}
    for r in W.raters:
        groups.setdefault(round(W.t_r[r], 2), []).append(r)
    return sorted((t, "=".join(rs)) for t, rs in groups.items())


def _band_labels(ax, items: list[tuple[float, str]], y_top: float, step: float) -> None:
    """세로 경계선 라벨을 축 안 상단 띠에 가로로 둔다. 가까우면 줄을 바꿔 겹치지 않게."""
    xmin, xmax = ax.get_xlim()
    last_x, row = None, 0
    for x, text in items:
        row = (row + 1) % 2 if last_x is not None and abs(x - last_x) < 0.09 * (xmax - xmin) else 0
        ax.text(x, y_top - row * step, text, ha="center", va="top", fontsize=7.5, color=INK2,
                bbox=dict(boxstyle="square,pad=0.1", fc=SURFACE, ec="none"), zorder=4)
        last_x = x


def _label_points(ax, xs, ys, names, idx, min_gap: float = 0.07) -> None:
    """선택한 점에만 이름표. 화면에서 너무 가까운 점은 건너뛴다."""
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    placed: list[tuple[float, float]] = []
    for i in idx:
        u, v = (xs[i] - x0) / (x1 - x0), (ys[i] - y0) / (y1 - y0)
        if any(abs(u - a) < min_gap and abs(v - b) < min_gap for a, b in placed):
            continue
        placed.append((u, v))
        ax.annotate(names[i], (xs[i], ys[i]), xytext=(6, 5), textcoords="offset points", fontsize=7.5,
                    color=INK2, zorder=5)


def _jitter(n: int, amp: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-amp, amp, n)


def _sig(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


# ---------------------------------------------------------------- 데이터·모델


class World:
    """그림에 필요한 값을 한 번에 계산해 둔다."""

    def __init__(self, s, g):
        from svmtrial import concepts as C
        from svmtrial import labels as L
        from svmtrial.features import load_X
        from svmtrial.modeling import fit_hierarchical

        X = load_X(s, g)
        X.index = X.index.astype(str)
        concepts = C.load_approved_concepts(s, g)
        unstable = set(C.load_scoring_meta(s, g).get("unstable_concepts") or [])
        crit = [c.id for c in concepts if c.id in X.columns and c.id not in unstable]
        Xc = X[crit].fillna(0.0)
        self.crit = [c for c in crit if Xc[c].nunique() > 1]
        self.X = Xc[self.crit]
        self.label = {c.id: c.question for c in concepts}
        self.ctype = {c.id: c.type for c in concepts}
        self.source = {c.id: c.source for c in concepts}

        t = L.load_targets(s, g)
        t["doc_id"] = t["doc_id"].astype(str)
        t = t.set_index("doc_id").loc[self.X.index]
        self.ratio = (t["n_pass"] / t["n_raters"]).to_numpy(float)
        self.cls = np.where(t["n_pass"] == t["n_raters"], "전원 Pass",
                            np.where(t["n_pass"] == 0, "전원 Fail", "일부 Fail"))
        self.long = L.load_long(s, g)

        fit = fit_hierarchical(s, self.long, self.X, self.crit)
        self.w = np.array([fit["weights"][c] for c in self.crit])
        self.b = float(fit["intercept"])
        self.beta = {str(r): float(v) for r, v in fit["rater_effect"].items()}
        self.raters = sorted(self.beta)
        self.score = self.X.to_numpy(float) @ self.w
        self.t_r = {r: -(self.b + self.beta[r]) for r in self.raters}
        self.t_all = self._all_pass_threshold()
        self.p_all = self.all_pass(self.score)

    def all_pass(self, s):
        return np.prod([_sig(np.asarray(s) + self.b + self.beta[r]) for r in self.raters], axis=0)

    def _all_pass_threshold(self) -> float:
        lo, hi = -60.0, 60.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if self.all_pass(mid) < 0.5:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def ortho_pc(self, k: int = 2) -> np.ndarray:
        """w 방향을 뺀 나머지 공간의 주성분 (결정축과 직교)."""
        Z = self.X.to_numpy(float) - self.X.to_numpy(float).mean(axis=0)
        u = self.w / np.linalg.norm(self.w)
        R = Z - np.outer(Z @ u, u)
        _, _, vt = np.linalg.svd(R, full_matrices=False)
        return vt[:k]

    def short(self, cid: str, n: int = 26) -> str:
        from svmtrial.textutil import quoted_phrase

        q = quoted_phrase(self.label.get(cid, cid)) or self.label.get(cid, cid)
        return f"{cid} {q[:n]}" + ("…" if len(q) > n else "")


def _rater_lines(ax, W: World, x_of, vertical: bool = True, slope_line=None) -> list:
    """평가자별 경계(점선) + 정답 경계(실선). 반환: 범례 항목."""
    for t, names in _rater_groups(W):
        if vertical:
            ax.axvline(x_of(t), color=INK2, lw=1.0, ls=(0, (4, 3)), zorder=2)
        else:
            slope_line(t, dict(color=INK2, lw=1.0, ls=(0, (4, 3)), zorder=2), names)
    if vertical:
        ax.axvline(x_of(W.t_all), color=INK, lw=2.0, zorder=2)
    else:
        slope_line(W.t_all, dict(color=INK, lw=2.0, zorder=2), None)
    return [(Line2D([], [], color=INK, lw=2), "정답 경계 (전원 Pass 50%)"),
            (Line2D([], [], color=INK2, lw=1, ls=(0, (4, 3))), "평가자별 경계")]


# ---------------------------------------------------------------- 그림


def v1_decision_axis(W: World, out: Path, arrows: bool = False) -> Path:
    pc = W.ortho_pc(1)[0]
    x0 = W.score - W.t_all
    y0 = (W.X.to_numpy(float) - W.X.to_numpy(float).mean(axis=0)) @ pc
    jx, jy = _jitter(len(x0), 0.10, 1), _jitter(len(x0), 0.07, 2)
    fig, ax = plt.subplots(figsize=(8.6, 5.9))
    lo, hi = min(x0.min(), *(W.t_r[r] - W.t_all for r in W.raters)) - 0.8, max(x0.max(), 0) + 0.8
    span = y0.max() - y0.min()
    ax.set_xlim(lo, hi)
    ax.set_ylim(y0.min() - 0.12 * span, y0.max() + 0.30 * span)
    ax.axvspan(0, hi, color=PASS, alpha=0.06, lw=0, zorder=0)
    extra = _rater_lines(ax, W, lambda t: t - W.t_all)
    if arrows:
        _counterfactual_arrows(ax, W, x0, y0, pc)
    _scatter(ax, x0 + jx, y0 + jy, W.cls)
    _band_labels(ax, [(t - W.t_all, n) for t, n in _rater_groups(W)] + [(0.0, "정답 경계")],
                 y0.max() + 0.27 * span, 0.075 * span)
    if not arrows:
        _label_points(ax, x0 + jx, y0 + jy, list(W.X.index), list(np.argsort(np.abs(x0))[:8]))
    ax.set_xlabel("정답 경계까지 거리  (w·x - 경계값, 0 = 경계, 오른쪽 = 전원 Pass 쪽)")
    ax.set_ylabel("경계와 무관한 차이 (직교 주성분 1)")
    if arrows:
        _frame(fig, "반사실 화살표 — Fail 문서가 경계를 넘으려면",
               "화살표 = 빠진 기준을 가중치 큰 것부터 채웠을 때의 이동 (가장 적은 개수). 굵기 = 같은 이동을 하는 문서 수")
    else:
        _frame(fig, "결정축 투영 — 경계면을 정확히 보존하는 2D",
               "x축이 곧 경계면의 법선(w). 세로 실선 = 정답 경계, 점선 = 평가자별 경계. 파란 바탕 = 전원 Pass 영역")
    _legend(ax, extra)
    f = out / ("v8_counterfactual_arrows.png" if arrows else "v1_decision_axis.png")
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def _counterfactual_arrows(ax, W: World, x0, y0, pc) -> None:
    """Fail 쪽 문서마다 최소 보완(빠진 기준을 w 큰 순으로) 후의 위치로 화살표. 같은 이동은 하나로 합친다."""
    from collections import Counter

    order = np.argsort(-W.w)
    moves: Counter = Counter()
    fixes: Counter = Counter()
    for i in range(len(x0)):
        if x0[i] >= 0:
            continue
        x = W.X.to_numpy(float)[i].copy()
        gained, picked = 0.0, []
        for k in order:
            if W.w[k] <= 0 or x[k] == 1.0:
                continue
            x[k] = 1.0
            picked.append(W.crit[k])
            gained += W.w[k]
            if x0[i] + gained > 0:
                break
        if not picked or x0[i] + gained <= 0:
            continue
        x1 = x0[i] + gained
        y1 = (x - W.X.to_numpy(float).mean(axis=0)) @ pc
        moves[(round(x0[i], 3), round(y0[i], 3), round(x1, 3), round(float(y1), 3))] += 1
        fixes.update(picked)
    for (xa, ya, xb, yb), n in moves.items():
        ax.annotate("", xy=(xb, yb), xytext=(xa, ya), zorder=2,
                    arrowprops=dict(arrowstyle="-|>", color=INK2, lw=0.5 + 0.3 * min(n, 6),
                                    shrinkA=4, shrinkB=4, alpha=0.55))
    top = ", ".join(f"{k} {v}건" for k, v in fixes.most_common(3))
    ax.text(0.99, 0.02, f"가장 흔한 보완: {top}", transform=ax.transAxes, ha="right", va="bottom",
            fontsize=8, color=INK2, bbox=dict(boxstyle="square,pad=0.2", fc=SURFACE, ec="none"), zorder=5)


def v2_additive(W: World, out: Path) -> Path:
    A = [i for i, c in enumerate(W.crit) if W.ctype.get(c) == "structure"]
    B = [i for i in range(len(W.crit)) if i not in A]
    if not A or not B:                                      # 유형이 한쪽뿐이면 가중치 순위로 번갈아 나눈다
        order = list(np.argsort(-np.abs(W.w)))
        A, B = order[0::2], order[1::2]
    Xv = W.X.to_numpy(float)
    xa, yb = Xv[:, A] @ W.w[A], Xv[:, B] @ W.w[B]
    jx, jy = _jitter(len(xa), 0.09, 3), _jitter(len(xa), 0.09, 4)
    fig, ax = plt.subplots(figsize=(8.6, 5.9))
    _frame(fig, "가산 분해 — 기준을 두 묶음으로 나눈 평면 (경계 정확)",
           "점수 = A + B 이므로 경계는 기울기 -1 직선. 묶음은 기준 유형(구조 / 정량·내용)으로 나눴다 — 사람이 정할 수 있다")
    x_lo, x_hi = xa.min() - 0.8, xa.max() + 0.8
    y_lo, y_hi = yb.min() - 0.7, yb.max() + 0.7
    xs = np.linspace(x_lo, x_hi, 200)
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    p1, p2 = ax.transData.transform([(0.0, 0.0), (1.0, -1.0)])
    angle = float(np.degrees(np.arctan2(p2[1] - p1[1], p2[0] - p1[0])))   # 화면에서 본 선의 기울기

    def line(t, kw, lab):
        ax.plot(xs, t - xs, **kw)
        if lab:                                   # 아래 가장자리에서 시작해 선을 따라 위로 (잘리지 않게)
            xl = min(max(t - (y_lo + 0.08), x_lo + 0.1), x_hi - 0.1)
            ax.text(xl, t - xl, lab, fontsize=7.5, color=INK2, rotation=angle, rotation_mode="anchor",
                    ha="right", va="bottom", bbox=dict(boxstyle="square,pad=0.05", fc=SURFACE, ec="none"),
                    clip_on=True)

    ax.fill_between(xs, W.t_all - xs, y_hi + 1, color=PASS, alpha=0.06, lw=0, zorder=0)
    extra = _rater_lines(ax, W, None, vertical=False, slope_line=line)
    _scatter(ax, xa + jx, yb + jy, W.cls)
    ax.set_xlabel("묶음 A 점수 = " + " + ".join(W.crit[i] for i in A) + "  (기준 있음 × 가중치의 합)")
    ax.set_ylabel("묶음 B 점수 = " + " + ".join(W.crit[i] for i in B))
    _legend(ax, extra)
    f = out / "v2_additive_split.png"
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def _plane_polygon(n, d: float, lo, hi):
    """평면 n·p = d 와 상자 [lo, hi] 의 교차 다각형. 꼭짓점을 평면 위 각도 순으로 정렬한다."""
    import itertools

    n = np.asarray(n, float)
    corners = [np.array(c, float) for c in itertools.product(*zip(lo, hi, strict=False))]
    pts = []
    for a, b in itertools.combinations(range(8), 2):
        pa, pb = corners[a], corners[b]
        if int((pa != pb).sum()) != 1:              # 상자의 모서리만
            continue
        fa, fb = n @ pa - d, n @ pb - d
        if fa == 0:
            pts.append(pa)
        elif fa * fb < 0:
            pts.append(pa + fa / (fa - fb) * (pb - pa))
    if len(pts) < 3:
        return None
    P = np.unique(np.round(np.array(pts), 9), axis=0)
    c = P.mean(axis=0)
    u = np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 * np.linalg.norm(n) else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(n / np.linalg.norm(n), u)
    return P[np.argsort(np.arctan2((P - c) @ v, (P - c) @ u))]


def _draw_plane(ax, n, d: float, lo, hi) -> None:
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    poly = _plane_polygon(n, d, lo, hi)
    if poly is not None:
        ax.add_collection3d(Poly3DCollection([poly], facecolor=MUTED, alpha=0.22, edgecolor=INK2, linewidth=0.9))


def _ax3d(fig):
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(SURFACE)
    for a in (ax.xaxis, ax.yaxis, ax.zaxis):
        a.set_pane_color((0.988, 0.988, 0.984, 1.0))
        a._axinfo["grid"].update(color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=8)
    return ax


def _frame3d(fig, ax, title: str, sub: str) -> None:
    fig.text(0.012, 0.975, title, fontsize=12.5, fontweight="bold", color=INK, va="top")
    fig.text(0.012, 0.935, sub, fontsize=8.5, color=INK2, va="top")
    fig.subplots_adjust(top=0.98, bottom=0.02, left=0.0, right=0.96)
    h, lab = ax.get_legend_handles_labels()
    fig.legend(h, lab, loc="upper left", bbox_to_anchor=(0.012, 0.905), ncol=3, fontsize=8.3, labelcolor=INK2,
               frameon=False)


def v3_cube(W: World, out: Path) -> Path:
    top = list(np.argsort(-np.abs(W.w))[:3])
    rest = [i for i in range(len(W.crit)) if i not in top]
    Xv = W.X.to_numpy(float)
    c_rest = float(Xv[:, rest].mean(axis=0) @ W.w[rest]) if rest else 0.0
    j = [_jitter(len(Xv), 0.11, 10 + k) for k in range(3)]
    fig = plt.figure(figsize=(8.2, 6.6))
    ax = _ax3d(fig)
    lo, hi = (-0.25, -0.25, -0.25), (1.25, 1.25, 1.25)
    _draw_plane(ax, W.w[top], W.t_all - c_rest, lo, hi)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    for name, (color, marker) in CLASSES.items():
        m = W.cls == name
        if m.any():
            ax.scatter(Xv[m, top[0]] + j[0][m], Xv[m, top[1]] + j[1][m], Xv[m, top[2]] + j[2][m], s=34, c=color,
                       marker=marker, edgecolors=SURFACE, linewidths=0.8, depthshade=False,
                       label=f"{name} ({int(m.sum())})")
    ax.set_xlabel(W.short(W.crit[top[0]], 18), labelpad=8, color=INK2)
    ax.set_ylabel(W.short(W.crit[top[1]], 18), labelpad=8, color=INK2)
    ax.set_zlabel(W.short(W.crit[top[2]], 18), labelpad=8, color=INK2)
    for setter in (ax.set_xticks, ax.set_yticks, ax.set_zticks):
        setter([0, 1])
    ax.set_xticklabels(["없음", "있음"])
    ax.set_yticklabels(["없음", "있음"])
    ax.set_zticklabels(["없음", "있음"])
    ax.view_init(elev=20, azim=-58)
    _frame3d(fig, ax, "기준 3개 = 3차원 큐브 (가장 직관적, 경계면은 근사)",
             "모서리 8개 = 세 기준의 있음/없음 조합. 회색 면 = 정답 경계 (나머지 기준은 평균으로 고정한 단면)")
    f = out / "v3_criteria_cube_3d.png"
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def v4_partial_3d(W: World, out: Path) -> Path:
    top = list(np.argsort(-np.abs(W.w))[:2])
    rest = [i for i in range(len(W.crit)) if i not in top]
    Xv = W.X.to_numpy(float)
    z = Xv[:, rest] @ W.w[rest] if rest else np.zeros(len(Xv))
    jx, jy, jz = _jitter(len(Xv), 0.11, 20), _jitter(len(Xv), 0.11, 21), _jitter(len(Xv), 0.06, 22)
    fig = plt.figure(figsize=(8.2, 6.6))
    ax = _ax3d(fig)
    zlo, zhi = min(z.min(), W.t_all - W.w[top].sum()) - 0.4, max(z.max(), W.t_all) + 0.4
    lo, hi = (-0.25, -0.25, zlo), (1.25, 1.25, zhi)
    _draw_plane(ax, [W.w[top[0]], W.w[top[1]], 1.0], W.t_all, lo, hi)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    for name, (color, marker) in CLASSES.items():
        m = W.cls == name
        if m.any():
            ax.scatter(Xv[m, top[0]] + jx[m], Xv[m, top[1]] + jy[m], z[m] + jz[m], s=34, c=color, marker=marker,
                       edgecolors=SURFACE, linewidths=0.8, depthshade=False, label=f"{name} ({int(m.sum())})")
    ax.set_xlabel(W.short(W.crit[top[0]], 18), labelpad=8, color=INK2)
    ax.set_ylabel(W.short(W.crit[top[1]], 18), labelpad=8, color=INK2)
    ax.set_zlabel("나머지 기준 점수", labelpad=6, color=INK2)
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(["없음", "있음"])
    ax.set_yticklabels(["없음", "있음"])
    ax.view_init(elev=16, azim=-62)
    _frame3d(fig, ax, "기준 2개 + 나머지 점수 = 3D (경계면 정확)",
             "높이 = 나머지 기준들의 가중합. 면 위 = 전원 Pass 쪽. 면의 기울기 = 두 기준의 가중치")
    f = out / "v4_partial_score_3d.png"
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def v5_curve(W: World, out: Path) -> Path:
    s = W.score
    xs = np.linspace(s.min() - 1.5, s.max() + 1.5, 300)
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    for r in W.raters:
        ax.plot(xs, _sig(xs + W.b + W.beta[r]), color=INK2, lw=1.0, ls=(0, (4, 3)))
    order = " · ".join(n for _, n in _rater_groups(W))
    ax.text(0.99, 0.30, f"점선 왼쪽부터(관대 → 엄격): {order}", transform=ax.transAxes, ha="right", fontsize=8,
            color=INK2, bbox=dict(boxstyle="square,pad=0.2", fc=SURFACE, ec="none"))
    pa = W.all_pass(xs)
    ax.plot(xs, pa, color=INK, lw=2.0, label="P(전원 Pass) — 정답 곡선")
    ax.axvline(W.t_all, color=INK, lw=0.8, ls=":", zorder=1)
    ax.text(W.t_all, 0.03, " 50% 지점 = 정답 경계", fontsize=8, color=INK2)
    jx, jy = _jitter(len(s), 0.06, 30), _jitter(len(s), 0.025, 31)
    _scatter(ax, s + jx, W.ratio + jy, W.cls)
    ax.set_xlim(xs[0], xs[-1])
    ax.set_ylim(-0.06, 1.08)
    ax.set_xlabel("문서 점수 w·x  (기준을 채울수록 오른쪽)")
    ax.set_ylabel("확률 / 실제 Pass 비율")
    _frame(fig, "정답 곡선 — 점수에 따른 Pass 확률 (로지스틱)",
           "실선 = 평가자 전원 Pass 확률, 점선 = 평가자별 Pass 확률, 점 = 각 문서의 실제 Pass 비율 (평가자 수만큼 계단)")
    extra = [(Line2D([], [], color=INK2, lw=1, ls=(0, (4, 3))), "평가자별 Pass 확률")]
    _legend(ax, extra)
    f = out / "v5_answer_curve.png"
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def v6_heatmap(W: World, out: Path) -> Path:
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    order = np.argsort(-W.score)
    cols = list(np.argsort(-W.w))
    M = W.X.to_numpy(float)[order][:, cols]
    long = W.long.copy()
    long["doc_id"] = long["doc_id"].astype(str)
    piv = long.pivot_table(index="doc_id", columns="rater", values="y", aggfunc="first").reindex(W.X.index[order])
    R = piv[W.raters].to_numpy(float)
    n = len(order)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.6, max(5.5, 0.11 * n + 1.6)), sharey=True,
                                 gridspec_kw={"width_ratios": [len(cols), len(W.raters)], "wspace": 0.04})
    a1.imshow(M, aspect="auto", cmap=ListedColormap([ABSENT, PRESENT]), vmin=0, vmax=1, interpolation="nearest")
    Rc = np.where(np.isnan(R), 0.5, R)
    a2.imshow(Rc, aspect="auto", cmap=ListedColormap([FAIL, SURFACE, PASS]), vmin=0, vmax=1,
              interpolation="nearest")
    cut = int((W.score[order] >= W.t_all).sum()) - 0.5
    for a, ncol in ((a1, len(cols)), (a2, len(W.raters))):
        a.grid(False)
        for xv in np.arange(0.5, ncol - 0.5):          # 셀 사이 표면색 간격 (테두리 대신)
            a.axvline(xv, color=SURFACE, lw=2.0)
        for yv in np.arange(0.5, n - 0.5):
            a.axhline(yv, color=SURFACE, lw=0.6)
        a.axhline(cut, color=INK, lw=2.2)
        a.tick_params(length=0)
        for sp in a.spines.values():
            sp.set_visible(False)
    a1.set_xticks(range(len(cols)))
    a1.set_xticklabels([W.crit[i] for i in cols], fontsize=8, color=INK2)
    a1.xaxis.set_ticks_position("top")
    a2.set_xticks(range(len(W.raters)))
    a2.set_xticklabels(W.raters, fontsize=8, color=INK2, rotation=0)
    a2.xaxis.set_ticks_position("top")
    a1.set_yticks(range(n))
    a1.set_yticklabels(W.X.index[order], fontsize=5.5, color=MUTED)
    a2.text(len(W.raters) - 0.5, cut, "  ◀ 정답 경계", ha="left", va="center", fontsize=8, color=INK,
            clip_on=False)
    fig.suptitle("정렬 히트맵 — 문서 × 기준 (점수순) + 평가자 판정", x=0.02, ha="left", fontsize=12,
                 fontweight="bold", color=INK)
    fig.text(0.02, 0.945, "위로 갈수록 점수가 높다. 굵은 선 위 = 전원 Pass 쪽. 비슷한 행이 모인 곳 = 같은 성격의 문서 묶음",
             fontsize=8.5, color=INK2)
    handles = [Patch(fc=PRESENT, label="기준 있음"), Patch(fc=ABSENT, label="기준 없음"),
               Patch(fc=PASS, label="평가자 p"), Patch(fc=FAIL, label="평가자 f")]
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=8.5, labelcolor=INK2,
               bbox_to_anchor=(0.5, 0.0))
    fig.subplots_adjust(left=0.1, right=0.90, top=0.88, bottom=0.06)
    f = out / "v6_sorted_heatmap.png"
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def v7_similarity_map(W: World, out: Path) -> Path:
    from sklearn.manifold import MDS

    Xv = W.X.to_numpy(float)
    D = (np.abs(Xv[:, None, :] - Xv[None, :, :]) * np.abs(W.w)).sum(axis=2)  # 가중 해밍: 중요한 기준이 다를수록 멀다
    emb = MDS(n_components=2, dissimilarity="precomputed", random_state=0, n_init=4,
              normalized_stress="auto").fit_transform(D)
    jx, jy = _jitter(len(emb), 0.07, 40), _jitter(len(emb), 0.07, 41)
    ex, ey = emb[:, 0] + jx, emb[:, 1] + jy
    gx, gy = np.meshgrid(np.linspace(ex.min() - 0.6, ex.max() + 0.6, 160),
                         np.linspace(ey.min() - 0.6, ey.max() + 0.6, 160))
    d2 = (gx[..., None] - emb[:, 0]) ** 2 + (gy[..., None] - emb[:, 1]) ** 2
    k = np.exp(-d2 / (2 * 0.35 ** 2))
    pz = (k * W.p_all).sum(axis=2) / np.maximum(k.sum(axis=2), 1e-9)
    fig, ax = plt.subplots(figsize=(8.6, 5.9))
    ax.contourf(gx, gy, pz, levels=[0.5, 1.01], colors=[PASS], alpha=0.07)
    ax.contour(gx, gy, pz, levels=[0.5], colors=[INK], linewidths=1.6)
    _scatter(ax, ex, ey, W.cls)
    # 화면에서 한 덩어리로 보이는 문서 수를 표시한다 (가까운 자리는 합친다 — 라벨이 겹치지 않게)
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    blobs: list[list] = []                      # [u, v, 개수, x, y]
    for i in np.argsort([-(Xv == Xv[k]).all(axis=1).sum() for k in range(len(Xv))]):
        u, v = (emb[i, 0] - x0) / (x1 - x0), (emb[i, 1] - y0) / (y1 - y0)
        hit = next((b for b in blobs if abs(b[0] - u) < 0.06 and abs(b[1] - v) < 0.06), None)
        if hit:
            hit[2] += 1
        else:
            blobs.append([u, v, 1, emb[i, 0], emb[i, 1]])
    for _, _, cnt, bx, by in blobs:
        if cnt >= 3:
            ax.annotate(f"×{cnt}", (bx, by), xytext=(14, -14), textcoords="offset points", fontsize=7.5,
                        color=INK2, zorder=6, bbox=dict(boxstyle="square,pad=0.1", fc=SURFACE, ec="none"))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("MDS 1  (축 자체에는 의미가 없다 — 점 사이 거리만 의미가 있다)")
    ax.set_ylabel("MDS 2")
    _frame(fig, "유사도 지도 — 가까운 점 = 기준 구성이 비슷한 문서",
           "거리 = 서로 다른 기준의 가중치 합(가중 해밍). 곡선 = 문서별 예측 확률을 부드럽게 이은 근사 경계 (선형 경계가 휘어 보인다)")
    _legend(ax, [(Line2D([], [], color=INK, lw=1.6), "근사 경계 (전원 Pass 50%)")])
    f = out / "v7_similarity_map.png"
    fig.savefig(f, dpi=150)
    plt.close(fig)
    return f


def points_table(W: World, out: Path) -> Path:
    """그림의 표 형태 짝 — 문서별 점수·경계까지 거리·확률·분류와 기준값."""
    pc = W.ortho_pc(1)[0]
    df = W.X.copy()
    df.insert(0, "class", W.cls)
    df.insert(1, "pass_ratio", np.round(W.ratio, 3))
    df.insert(2, "score_wx", np.round(W.score, 4))
    df.insert(3, "dist_to_boundary", np.round(W.score - W.t_all, 4))
    df.insert(4, "p_all_pass", np.round(W.p_all, 4))
    df.insert(5, "ortho_pc1", np.round((W.X.to_numpy(float) - W.X.to_numpy(float).mean(axis=0)) @ pc, 4))
    f = out / "viz_points.csv"
    df.sort_values("score_wx", ascending=False).to_csv(f, encoding="utf-8")
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--group", "-g", default=None)
    ap.add_argument("--out", default="docs/img/viz")
    a = ap.parse_args()

    from svmtrial.config import load_settings
    from svmtrial.groups import discover_groups, parse_group

    s = load_settings(ROOT, config_path=a.config)
    g = parse_group(a.group) if a.group else discover_groups(s)[0]
    out = (ROOT / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    _style()
    W = World(s, g)
    print(f"그룹 {g.key}: 문서 {len(W.X)}건, 기준(차원) {len(W.crit)}개 {W.crit}, 평가자 {W.raters}")
    print("가중치 w: " + ", ".join(f"{c} {v:+.2f}" for c, v in zip(W.crit, W.w, strict=False)))
    print(f"정답 경계 w·x = {W.t_all:+.2f} | 평가자별: " + ", ".join(f"{r} {W.t_r[r]:+.2f}" for r in W.raters))
    files = [v1_decision_axis(W, out), v2_additive(W, out), v3_cube(W, out), v4_partial_3d(W, out),
             v5_curve(W, out), v6_heatmap(W, out), v7_similarity_map(W, out), v1_decision_axis(W, out, arrows=True)]
    files.append(points_table(W, out))
    for f in files:
        print("  →", f.relative_to(ROOT) if f.is_relative_to(ROOT) else f)
    return 0 if math.isfinite(W.t_all) else 1


if __name__ == "__main__":
    sys.exit(main())
