import streamlit as st
import fitz
import pytesseract
import os
import re
import base64
import tempfile
from groq import Groq
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from PIL import Image
import streamlit.components.v1 as components

# ── Page config ──────────────────────────────────────────
st.set_page_config(
    page_title="Chat with your PDF",
    page_icon="💬",
    layout="wide"
)

# ── Session state init ────────────────────────────────────
if "full_text" not in st.session_state:
    st.session_state.full_text = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "pdf_ready" not in st.session_state:
    st.session_state.pdf_ready = False
if "selectable_pdf_bytes" not in st.session_state:
    st.session_state.selectable_pdf_bytes = None
if "current_page" not in st.session_state:
    st.session_state.current_page = 1
if "total_pages" not in st.session_state:
    st.session_state.total_pages = 1

# ── Groq client ──────────────────────────────────────────
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ── Helper functions ─────────────────────────────────────
def pdf_to_images(pdf_path, output_folder):
    pdf = fitz.open(pdf_path)
    image_paths = []
    for i in range(len(pdf)):
        page = pdf[i]
        mat = fitz.Matrix(300/72, 300/72)
        img = page.get_pixmap(matrix=mat)
        path = os.path.join(output_folder, f"page_{i+1}.png")
        img.save(path)
        image_paths.append(path)
    return image_paths

def clean_text(text):
    text = re.sub(r'-\s+', '', text)
    text = re.sub(r'[|\\•§©®™]', '', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'^\d+$', '', text, flags=re.MULTILINE)
    return text.strip()

def run_ocr_paged(image_paths):
    full_text = ""
    for i, path in enumerate(image_paths):
        img = Image.open(path)
        text = pytesseract.image_to_string(img)
        cleaned = clean_text(text)
        full_text += f"\n[Page {i+1}]\n{cleaned}\n"
    return full_text

def create_selectable_pdf(image_paths, output_path):
    c = canvas.Canvas(output_path, pagesize=A4)
    page_width, page_height = A4
    for image_path in image_paths:
        img = Image.open(image_path)
        img_w, img_h = img.size
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        for i, text in enumerate(data['text']):
            if text.strip() and int(data['conf'][i]) > 40:
                x = (data['left'][i] / img_w) * page_width
                y = page_height - ((data['top'][i] + data['height'][i]) / img_h) * page_height
                c.setFont("Helvetica", 10)
                c.drawString(x, y, text)
        c.showPage()
    c.save()

def render_pdf_viewer(pdf_bytes, page_num=1):
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    pdf_url = f"data:application/pdf;base64,{b64}#page={page_num}"
    iframe_html = f"""
    <iframe
        src="{pdf_url}"
        width="100%"
        height="800px"
        style="border: 1px solid #ddd; border-radius: 8px;"
        type="application/pdf"
    ></iframe>
    """
    components.html(iframe_html, height=820)

def call_llm(user_message, full_text, chat_history):
    messages = [
        {
            "role": "system",
            "content": f"""You are an intelligent document assistant.
You have been given the full text of a scanned document, with each page marked as [Page N].
Answer the employee's questions based ONLY on this document.
Be thorough, professional, and helpful.

IMPORTANT CITATION RULE:
Whenever you reference information from the document, you MUST cite the page number like this: [Page 3]
Always include page citations so the employee can verify the source.
Multiple citations are fine: [Page 3] [Page 7]

Document Text:
{full_text}"""
        }
    ]
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=messages,
        temperature=0.2,
        max_tokens=4000
    )
    return response.choices[0].message.content

def render_chat_message_with_citations(msg_content):
    parts = re.split(r'(\[Page \d+\])', msg_content)
    for part in parts:
        match = re.match(r'\[Page (\d+)\]', part)
        if match:
            page_num = int(match.group(1))
            if st.button(f"📄 Page {page_num}", key=f"cite_{page_num}_{hash(msg_content)}"):
                st.session_state.current_page = page_num
                st.rerun()
        else:
            if part.strip():
                st.markdown(part)

def is_selectable_pdf_request(text):
    keywords = ["selectable pdf", "selectable", "text pdf", "copyable pdf", "searchable pdf"]
    return any(k in text.lower() for k in keywords)

# ── UI ────────────────────────────────────────────────────
st.title("💬 Chat with your PDF")
st.caption("Upload a scanned PDF — ask anything, click citations to jump to source pages")

# ── Upload section ────────────────────────────────────────
if not st.session_state.pdf_ready:
    uploaded_file = st.file_uploader("Upload your scanned PDF", type="pdf")

    if uploaded_file:
        st.success(f"✅ Uploaded: {uploaded_file.name}")

        if st.button("📖 Process PDF", type="primary"):
            tmpdir = tempfile.mkdtemp()

            pdf_path = os.path.join(tmpdir, "input.pdf")
            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.read())

            with st.status("Processing your PDF... please wait"):
                st.write("📄 Converting pages to images...")
                image_paths = pdf_to_images(pdf_path, tmpdir)
                total_pages = len(image_paths)
                st.write(f"✅ {total_pages} pages found")

                st.write("🔍 Running OCR with page tracking...")
                full_text = run_ocr_paged(image_paths)
                st.write(f"✅ Text extracted — {len(full_text)} characters")

                st.write("📝 Creating selectable PDF...")
                selectable_pdf_path = os.path.join(tmpdir, "selectable.pdf")
                create_selectable_pdf(image_paths, selectable_pdf_path)
                with open(selectable_pdf_path, "rb") as f:
                    st.session_state.selectable_pdf_bytes = f.read()
                st.write("✅ Done!")

            st.session_state.full_text = full_text
            st.session_state.pdf_ready = True
            st.session_state.total_pages = total_pages

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": f"✅ Your PDF has been processed! **{total_pages} pages** extracted.\n\nAsk me anything — I'll cite the exact page numbers so you can click and jump directly to the source.\n\nTry:\n- *Give me a summary*\n- *What are the covenants?*\n- *Who are the parties involved?*\n- *What are the key dates?*"
            })
            st.rerun()

# ── Main chat + PDF viewer layout ────────────────────────
if st.session_state.pdf_ready:

    with st.sidebar:
        st.markdown("### 📄 Document")
        st.success(f"✅ {st.session_state.total_pages} pages processed")

        st.divider()
        st.markdown("### 📥 Download")
        st.download_button(
            "📄 Selectable PDF",
            data=st.session_state.selectable_pdf_bytes,
            file_name="selectable_document.pdf",
            mime="application/pdf"
        )

        st.divider()
        st.markdown("### 💡 Quick Prompts")
        if st.button("⚡ Quick Summary"):
            st.session_state.chat_history.append({"role": "user", "content": "Give me a quick summary of the key points with page citations"})
            st.rerun()
        if st.button("📋 Deep Summary"):
            st.session_state.chat_history.append({"role": "user", "content": "Give me a comprehensive detailed summary with page citations"})
            st.rerun()
        if st.button("⚖️ Covenants & Schedules"):
            st.session_state.chat_history.append({"role": "user", "content": "Expand on all covenants and schedules in detail with page citations"})
            st.rerun()
        if st.button("📅 Key Dates"):
            st.session_state.chat_history.append({"role": "user", "content": "What are all the key dates and deadlines? Cite pages."})
            st.rerun()
        if st.button("👥 Parties Involved"):
            st.session_state.chat_history.append({"role": "user", "content": "Who are all the parties involved? Cite pages."})
            st.rerun()

        st.divider()
        st.markdown(f"### 📖 Viewing Page")
        st.info(f"Page {st.session_state.current_page} of {st.session_state.total_pages}")

        st.divider()
        if st.button("🗑️ Upload New PDF"):
            for key in ["full_text", "chat_history", "pdf_ready",
                        "selectable_pdf_bytes", "total_pages"]:
                st.session_state[key] = "" if key == "full_text" else [] if key == "chat_history" else False if key == "pdf_ready" else None if key == "selectable_pdf_bytes" else 1
            st.session_state.current_page = 1
            st.rerun()

    # Split layout
    chat_col, pdf_col = st.columns([1, 1])

    with chat_col:
        st.markdown("### 💬 Chat")

        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                if msg["role"] == "assistant":
                    render_chat_message_with_citations(msg["content"])
                else:
                    st.markdown(msg["content"])

        if st.session_state.chat_history and st.session_state.chat_history[-1]["role"] == "user":
            last_user_msg = st.session_state.chat_history[-1]["content"]

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    if is_selectable_pdf_request(last_user_msg):
                        response = "Your selectable PDF is ready — click **Download** in the sidebar! 👈"
                    else:
                        response = call_llm(
                            last_user_msg,
                            st.session_state.full_text,
                            st.session_state.chat_history[:-1]
                        )
                    render_chat_message_with_citations(response)

            st.session_state.chat_history.append({"role": "assistant", "content": response})
            st.rerun()

        user_input = st.chat_input("Ask anything about your document...")
        if user_input:
            st.session_state.chat_history.append({"role": "user", "content": user_input})
            st.rerun()

    with pdf_col:
        st.markdown(f"### 📄 Document Viewer — Page {st.session_state.current_page}")
        if st.session_state.selectable_pdf_bytes:
            render_pdf_viewer(
                st.session_state.selectable_pdf_bytes,
                st.session_state.current_page
            )
