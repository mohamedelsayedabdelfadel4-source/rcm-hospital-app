import io
import json
import re
from difflib import SequenceMatcher

import pandas as pd
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image, ImageOps

# ============ لائحة الأسعار (عدّلها هنا: الاسم، الوحدة، السعر) ============
PRICES = [
    ("مفرش سرير", "عدد", 3.20), ("اسورة تعريف", "عدد", 1.33), ("جوانتي معقم", "عدد", 40.26),
    ("جوانتي لاتكس", "عدد فرد", 1.7), ("مسحه كحولية", "عدد", 0.23), ("سرنجة انسولين", "عدد", 3.49),
    ("سرنجة 3", "عدد", 3.37), ("سرنجة 5", "عدد", 2.15), ("سرنجة 10", "عدد", 2.72),
    ("سرنجة 20", "عدد", 3.70), ("سرنجة 50", "عدد", 7.55), ("جهاز وريد", "عدد", 6.07),
    ("كانيولا زرقا", "عدد", 7.76), ("كانيولا صفرا", "عدد", 5.17), ("سالوسيت", "عدد", 29.59),
    ("قطن", "قطعة", 2.07), ("دريسنج", "قطعة", 9.48), ("مفرش ترابيزه", "عدد", 16.89),
    ("ملايه سرير", "عدد", 9.22), ("رباط شاش 7.5", "عدد", 5.9), ("رباط شاش 10", "عدد", 6.66),
    ("رباط ضاغط 10×10", "عدد", 15.85), ("رباط ضاغط 15×15", "عدد", 22.96),
    ("سوفت باند 10", "عدد", 8.44), ("سوفت باند 15", "عدد", 12.55), ("جبسونا 7.5", "عدد", 16.14),
    ("جبسونا 10", "عدد", 22.63), ("جبسونا 15", "عدد", 33.20), ("شاش فازلين 10×10", "عدد", 1.68),
    ("شاش فازلين 10×20", "عدد", 3.24), ("شاش معقم 10×10", "عدد", 8.44), ("بلاستر 7.5", "10 سم", 4.58),
    ("بلاستر 10", "10 سم", 5.46), ("بيتادين 10", "عدد", 0.30), ("مشرط", "عدد", 2.07),
    ("ماسك اكسجين كبار", "عدد", 14.22), ("ماسك فاركولين", "عدد", 18.95), ("شريط سكر", "عدد", 2.91),
    ("شكاكه سكر", "عدد", 0.32), ("الكترود", "عدد", 2.41), ("جاون معقم", "عدد", 43.20),
    ("جاون غير معقم", "عدد", 21.56), ("خافض لسان", "عدد", 0.30), ("مثبت كانيولا", "عدد", 2.068),
    ("بلاستر", "1 سم", 0.46), ("زيلوكين", "عدد", 0.47), ("خيط", "عدد", 49.3),
    ("قسطرة بولية", "عدد", 22.2), ("كيس جمع بول", "عدد", 8.45),
]
NAMES = [p[0] for p in PRICES]
INFO = {p[0]: p for p in PRICES}

# ============ قواعد المستلزمات (عدّلها هنا) ============
# كل مجموعة = أصناف مقبولة، والأول هو اللي بيتضاف تلقائي
CANNULA = ["كانيولا زرقا", "كانيولا صفرا"]
PER_FLUID = [["جهاز وريد"], ["بلاستر 7.5", "بلاستر", "بلاستر 10"], ["قطن"], ["مسحه كحولية"]]
SYRINGES = ["سرنجة 5", "سرنجة 3", "سرنجة 10", "سرنجة 20"]
INSULIN = ["سرنجة انسولين"]
KINDS = {"محلول": "fluid", "أمبولة": "ampoule", "مضاد حيوي": "antibiotic",
         "CT": "ct", "سونار": "us", "تاني": "other"}
KIND_LABEL = {v: k for k, v in KINDS.items()}


def expected(orders):
    """المطلوب من العلاج: قائمة (مجموعة أصناف، العدد، السبب). كود عادي بدون ذكاء اصطناعي."""
    out = {}

    def add(group, qty, why):
        e = out.setdefault(tuple(group), {"group": group, "qty": 0, "why": []})
        e["qty"] += qty
        if why not in e["why"]:
            e["why"].append(why)

    for o in orders:
        kind = KINDS.get(o["kind"], "other")
        qty = o["qty"] if o["qty"] and o["qty"] > 0 else 1
        if kind == "fluid":
            for g in PER_FLUID:      # كل محلول = جهاز وريد + بلاستر + قطن + مسحة
                add(g, qty, "محلول")
        elif kind == "ampoule":      # كل أمبولة = سرنجة
            add(SYRINGES, qty, "أمبولة")
        elif kind == "antibiotic":   # مضاد حيوي = سرنجة انسولين
            add(INSULIN, 1, "مضاد حيوي")
    if any(KINDS.get(o["kind"]) == "fluid" for o in orders):
        add(CANNULA, 1, "محلول")     # الكانيولا مرة واحدة بس
    return list(out.values())


# ============ مطابقة الأسماء مع اللائحة (كود عادي) ============
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def norm(s):
    s = str(s).lower().translate(AR_DIGITS)
    s = re.sub("[أإآ]", "ا", s).replace("ى", "ي").replace("ة", "ه")
    s = re.sub("[\u064B-\u0652\u0640]", "", s)
    s = s.replace("×", "x").replace("*", "x")
    s = re.sub(r"[^\w.\s]", " ", s)
    s = re.sub(r"(\d)\s*x\s*(\d)", r"\1x\2", s)
    return re.sub(r"\s+", " ", s).strip()


def is_num(t):
    return re.fullmatch(r"\d+(\.\d+)?(x\d+(\.\d+)?)?", t) is not None


def tok(s):
    return [t[2:] if (not is_num(t) and len(t) > 3 and t.startswith("ال")) else t for t in norm(s).split()]


def sim(a, b):
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 0.9
    r = SequenceMatcher(None, a, b).ratio()
    return r if r >= 0.7 else 0.0


PARSED = [([t for t in tok(n) if not is_num(t)], [t for t in tok(n) if is_num(t)]) for n in NAMES]


def match(line):
    """بيرجّع (اسم الصنف أو None، هل المطابقة واضحة، الاحتمالات التانية)."""
    t = tok(line)
    iw = [x for x in t if not is_num(x)]
    inn = [x for x in t if is_num(x)]
    if not iw:
        return None, False, []
    scored = []
    for i, (pw, pn) in enumerate(PARSED):
        ws = sum(max(sim(w, x) for x in iw) for w in pw) / len(pw)
        cov = sum(1 for x in iw if any(sim(w, x) > 0 for w in pw)) / len(iw)
        s = ws * 0.7 + cov * 0.3
        if pn and inn:
            s *= 0.3 + 0.7 * (sum(1 for x in pn if x in inn) / len(pn))
        elif not pn and inn:
            s *= 0.9
        scored.append((s, i))
    scored.sort(reverse=True)
    if scored[0][0] < 0.5:
        return None, False, []
    close = [i for s, i in scored if s >= 0.5 and s >= scored[0][0] - 0.12][:5]
    return NAMES[close[0]], len(close) == 1, [NAMES[i] for i in close[1:]]


# ============ قراءة الصور (الذكاء الاصطناعي هنا بس) ============
def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


SHEET_PROMPT = """This is the front sheet of a hospital patient file in Egypt (handwritten, Arabic and English). It contains a diagnosis and treatment orders. Return ONLY JSON:
{"diagnosis_original":"<diagnosis exactly as written>","diagnosis_arabic":"<clear Arabic translation: Arabic medical term followed by the English term in parentheses; keep drug names, doses and abbreviations in English>","orders":[{"text":"<one treatment or investigation order exactly as written>","kind":"fluid|ampoule|antibiotic|ct|us|other","qty":<number of units written, for example 2 amp, otherwise 1>}]}
kind: fluid = IV infusion bottle or bag (saline, ringer, dextrose, ...); antibiotic = any antibiotic; ampoule = any other injectable given from an ampoule or vial; ct = CT scan; us = ultrasound (سونار); other = anything else (tablets, oral, dressings, other imaging).
Mark illegible words as ؟. Never guess and never add an order that is not written."""

SUPPLIES_PROMPT = """This is a photo of a hospital supply issue form (إذن صرف) from Egypt, handwritten in Arabic. List every written line in order with its quantity. Return ONLY JSON:
{"lines":[{"name":"<item name exactly as written, keep size numbers that belong to the name>","qty":<number, or null if unreadable>}]}
Convert Arabic-Indic digits to normal numbers and fractions like ½ to 0.5. Ignore patient data, dates, prices and headers. Do not translate, correct or match anything to a list. Write ؟ for illegible words."""


def prep(f):
    im = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
    im.thumbnail((1800, 1800))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def read_photos(prompt, files):
    client = genai.Client(api_key=secret("GEMINI_API_KEY"))
    parts = [types.Part.from_bytes(data=prep(f), mime_type="image/jpeg") for f in files]
    r = client.models.generate_content(
        model=secret("GEMINI_MODEL", "gemini-flash-latest"),
        contents=parts + [prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    return json.loads(r.text)


# ============ الواجهة ============
def main():
    st.set_page_config(page_title="مراجعة مستلزمات المريض", layout="centered")
    st.markdown("<style>.stApp{direction:rtl}p,label,h1,h2,h3,.stAlert{text-align:right}</style>",
                unsafe_allow_html=True)
    ss = st.session_state
    ss.setdefault("orders", pd.DataFrame({"الأمر": pd.Series(dtype=str), "النوع": pd.Series(dtype=str),
                                          "العدد": pd.Series(dtype=float)}))
    ss.setdefault("rows", pd.DataFrame({"المكتوب": pd.Series(dtype=str), "الصنف": pd.Series(dtype=str),
                                        "العدد": pd.Series(dtype=float), "ملاحظة": pd.Series(dtype=str)}))
    ss.setdefault("over", 0)
    ss.setdefault("rver", 0)
    ss.setdefault("diag", None)

    st.title("مراجعة مستلزمات المريض")
    st.caption("الذكاء الاصطناعي بيقرأ الصور بس. الأسعار والقواعد من اللائحة والكود، مش منه.")

    pw = secret("APP_PASSWORD")
    if pw and st.text_input("كلمة السر", type="password") != pw:
        st.stop()

    # --- 1) وش الشيت ---
    st.subheader("١) وش الشيت (العلاج والتشخيص)")
    sheet_files = st.file_uploader("صوّر أو اختار صورة", type=["jpg", "jpeg", "png"],
                                   accept_multiple_files=True, key="sheet_up")
    if st.button("اقرأ الشيت", disabled=not sheet_files):
        with st.spinner("بقرأ الشيت..."):
            try:
                d = read_photos(SHEET_PROMPT, sheet_files)
                ss.diag = d
                ss.orders = pd.DataFrame({
                    "الأمر": [str(o.get("text", "")) for o in d.get("orders", [])],
                    "النوع": [KIND_LABEL.get(o.get("kind"), "تاني") for o in d.get("orders", [])],
                    "العدد": [float(o.get("qty") or 1) for o in d.get("orders", [])],
                })
                ss.over += 1
            except Exception as e:
                st.error(f"مشكلة في القراءة: {e}")

    if ss.diag:
        st.markdown("**التشخيص (زي ما اتقرا):**")
        st.text(ss.diag.get("diagnosis_original", ""))
        st.markdown("**بالعربي:**")
        st.info(ss.diag.get("diagnosis_arabic", ""))
        st.markdown("**أوامر العلاج (عدّل لو في غلط):**")
        orders_df = st.data_editor(
            ss.orders, key=f"o{ss.over}", num_rows="dynamic", use_container_width=True,
            column_config={
                "النوع": st.column_config.SelectboxColumn("النوع", options=list(KINDS)),
                "العدد": st.column_config.NumberColumn("العدد", min_value=0, step=1),
            })
        orders = [{"text": r["الأمر"], "kind": r["النوع"], "qty": r["العدد"]}
                  for _, r in orders_df.iterrows() if r["الأمر"] or r["النوع"]]
        for o in orders:
            if KINDS.get(o["kind"]) in ("ct", "us"):
                st.error(f"⚠️ فيه {o['kind']}: {o['text']}")
    else:
        orders = []

    # --- 2) المستلزمات ---
    st.subheader("٢) المستلزمات (التذكرة)")
    sup_files = st.file_uploader("صوّر أو اختار صورة التذكرة", type=["jpg", "jpeg", "png"],
                                 accept_multiple_files=True, key="sup_up")
    if st.button("اقرأ التذكرة", disabled=not sup_files):
        with st.spinner("بقرأ التذكرة..."):
            try:
                d = read_photos(SUPPLIES_PROMPT, sup_files)
                recs = []
                for l in d.get("lines", []):
                    name, ok, alts = match(str(l.get("name", "")))
                    note = "" if ok else ("راجع الصنف" + (" — أو: " + "، ".join(alts) if alts else ""))
                    q = l.get("qty")
                    recs.append({"المكتوب": str(l.get("name", "")), "الصنف": name,
                                 "العدد": float(q) if q is not None else None, "ملاحظة": note})
                df = pd.DataFrame(recs, columns=["المكتوب", "الصنف", "العدد", "ملاحظة"])
                df["العدد"] = pd.to_numeric(df["العدد"], errors="coerce")
                ss.rows = df
                ss.rver += 1
            except Exception as e:
                st.error(f"مشكلة في القراءة: {e}")

    st.markdown("**المستلزمات (عدّل الصنف أو العدد، أو ضيف سطر):**")
    rows_df = st.data_editor(
        ss.rows, key=f"r{ss.rver}", num_rows="dynamic", use_container_width=True,
        disabled=["المكتوب", "ملاحظة"],
        column_config={
            "الصنف": st.column_config.SelectboxColumn("الصنف", options=NAMES),
            "العدد": st.column_config.NumberColumn("العدد", min_value=0, step=0.5),
        })

    # --- الأسعار من اللائحة (من غير إجمالي) ---
    priced = [{"الصنف": r["الصنف"], "الوحدة": INFO[r["الصنف"]][1], "السعر لوحدة": INFO[r["الصنف"]][2]}
              for _, r in rows_df.iterrows() if r["الصنف"] in INFO]
    if priced:
        st.markdown("**الأسعار من اللائحة:**")
        st.dataframe(pd.DataFrame(priced), use_container_width=True, hide_index=True)
    if (rows_df["الصنف"].isna() | (rows_df["الصنف"] == "")).any() and len(rows_df):
        st.warning("في سطور من غير صنف. اختار الصنف من القايمة عشان يظهر سعره.")

    # --- المراجعة على العلاج ---
    if ss.diag:
        st.subheader("المراجعة على العلاج")
        exp = expected(orders)
        if not exp:
            st.caption("مفيش في العلاج محلول أو أمبولة أو مضاد حيوي يحتاج مستلزمات.")
        missing = []
        for e in exp:
            have = sum((r["العدد"] if pd.notna(r["العدد"]) and r["العدد"] > 0 else 1)
                       for _, r in rows_df.iterrows() if r["الصنف"] in e["group"])
            label = " أو ".join(e["group"][:2])
            why = "، ".join(e["why"])
            if have >= e["qty"]:
                st.success(f"✓ {label}: المطلوب {e['qty']:g}، المكتوب {have:g} ({why})")
            else:
                st.error(f"✗ {label}: المطلوب {e['qty']:g}، المكتوب {have:g} ({why})")
                missing.append({"المكتوب": "مقترح من العلاج", "الصنف": e["group"][0],
                                "العدد": e["qty"] - have, "ملاحظة": ""})
        if missing and st.button("ضيف كل الناقص"):
            ss.rows = pd.concat([rows_df, pd.DataFrame(missing)], ignore_index=True)
            ss.rver += 1
            st.rerun()


if __name__ == "__main__":
    main()
