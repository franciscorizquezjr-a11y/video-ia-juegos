"""
app.py — Streamlit Web Application Interface for AI Video Producer Pro

Features:
- Modern TikTok/Instagram luxury theme (purple, pink, cyan gradients, glassmorphism cards, neon accents)
- Pre-Production Feed tab: Scrapes and manages latest video trailers from Indie Games Discovery and GameTrailers (IGN)
- 1-Click Workflow routing from Feed to Single Mode Editor or Super Batch
- Scoped defaults: vertical_split_blur (Fondo borroso + Tarjeta), auto-cuts disabled by default, 1080p and 4K vertical options
- Single Mode & Super Batch with interactive mockups, hook generators, and length adjusters (Shorter/Longer)

Usage:
    streamlit run app.py
"""

import os
import sys
import json
import time
import re
import logging
from pathlib import Path
import streamlit as st

# Add current directory to sys.path
sys.path.append(str(Path(__file__).parent.resolve()))

from config import AppConfig
from pipeline import Pipeline
from utils import (
    PipelineError,
    setup_logging,
    validate_file_exists,
    CARD_PRESET_KEYS,
    export_card_preset
)
from scraper import TrailerDatabaseManager, refresh_all_trailers

logger = logging.getLogger(__name__)

# Set page configuration for modern wide dashboard
st.set_page_config(
    page_title="AI Video Producer Pro",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_modern_custom_theme():
    """Inject TikTok & Instagram inspired modern neon theme CSS into Streamlit."""
    st.markdown("""
    <style>
        /* Base page background */
        .stApp {
            background-color: #0b0f19;
            color: #f3f4f6;
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
        }

        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background-color: #111827 !important;
            border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
        }

        /* Brand Headings */
        .brand-header-title {
            background: linear-gradient(135deg, #a855f7 0%, #ec4899 50%, #06b6d4 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 900;
            font-size: 2.6rem;
            letter-spacing: -0.5px;
            margin-bottom: 0.2rem;
            text-align: center;
        }
        .brand-header-sub {
            color: #9ca3af;
            font-size: 1.05rem;
            text-align: center;
            margin-bottom: 1.8rem;
            font-weight: 500;
        }

        /* Modern Glassmorphism Cards */
        .trailer-card-box {
            background: rgba(17, 24, 39, 0.75);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 14px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
            transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
        }
        .trailer-card-box:hover {
            border-color: rgba(168, 85, 247, 0.5);
            box-shadow: 0 15px 35px -5px rgba(168, 85, 247, 0.25);
            transform: translateY(-2px);
        }

        /* Channel Badges */
        .badge-igd {
            background: linear-gradient(135deg, #7ca032, #4d681c);
            color: #ffffff;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 700;
            display: inline-block;
            box-shadow: 0 2px 8px rgba(124, 160, 50, 0.3);
        }
        .badge-guam {
            background: linear-gradient(135deg, #ff7d00, #c65d00);
            color: #ffffff;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 700;
            display: inline-block;
            box-shadow: 0 2px 8px rgba(255, 125, 0, 0.3);
        }

        /* Button Styling */
        .stButton > button {
            background: linear-gradient(135deg, #a855f7 0%, #ec4899 100%) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 10px !important;
            padding: 10px 20px !important;
            font-weight: 700 !important;
            transition: all 0.25s ease !important;
            box-shadow: 0 4px 15px rgba(168, 85, 247, 0.3) !important;
        }
        .stButton > button:hover {
            background: linear-gradient(135deg, #c084fc 0%, #f472b6 100%) !important;
            box-shadow: 0 6px 20px rgba(236, 72, 153, 0.4) !important;
            transform: translateY(-1px) !important;
        }

        /* Tabs Styling */
        .stTabs [data-baseweb="tab-list"] {
            gap: 12px;
            background: rgba(17, 24, 39, 0.7);
            padding: 8px;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 10px;
            padding: 10px 22px;
            font-weight: 700;
            color: #9ca3af;
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #a855f7 0%, #ec4899 100%) !important;
            color: #ffffff !important;
            box-shadow: 0 4px 15px rgba(168, 85, 247, 0.4) !important;
        }

        /* Input fields and Expanders */
        .stTextInput input, .stTextArea textarea, .stSelectbox select {
            background-color: #1f2937 !important;
            color: #ffffff !important;
            border-radius: 10px !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
        }
        .stExpander {
            background: rgba(17, 24, 39, 0.6) !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            border-radius: 14px !important;
        }
    </style>
    """, unsafe_allow_html=True)


def load_initial_config() -> AppConfig:
    """Load configuration from environment variables or .env file."""
    try:
        return AppConfig.from_env()
    except Exception:
        return AppConfig()


def get_predetermined_presets():
    """List all predetermined presets in the Presets folder."""
    presets_dir = Path("Presets")
    if not presets_dir.exists():
        return {}
    
    presets = {}
    for item in presets_dir.iterdir():
        if item.is_dir():
            json_files = list(item.glob("*.json"))
            image_files = list(item.glob("*.png")) + list(item.glob("*.jpg")) + list(item.glob("*.jpeg"))
            if json_files:
                presets[item.name] = {
                    "json_path": json_files[0],
                    "image_path": image_files[0] if image_files else None
                }
    return presets


def apply_predetermined_preset(preset_name, presets_dict, session_state, default_config):
    """Apply baseline JSON properties for a predetermined preset."""
    info = presets_dict.get(preset_name)
    if not info:
        return
    try:
        with open(info["json_path"], "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in CARD_PRESET_KEYS:
            if key in data:
                session_state[key] = data[key]
        if info["image_path"]:
            session_state["card_avatar_path"] = str(info["image_path"])
    except Exception as e:
        logger.error(f"Failed to load preset '{preset_name}': {e}")


def sync_preview_state(selected_preview_preset, presets_dict, default_config):
    """Sync session state variables with the selected preview preset."""
    last_preview = st.session_state.get("last_applied_preset")
    
    if selected_preview_preset.startswith("Predeterminado: "):
        preset_name = selected_preview_preset.replace("Predeterminado: ", "")
        if last_preview != preset_name:
            if last_preview == "Personalizado (Editor Libre)":
                for key in CARD_PRESET_KEYS:
                    st.session_state[f"backup_{key}"] = st.session_state.get(key)
            st.session_state["last_applied_preset"] = preset_name
            apply_predetermined_preset(preset_name, presets_dict, st.session_state, default_config)
    elif last_preview != "Personalizado (Editor Libre)":
        st.session_state["last_applied_preset"] = None
        for key in CARD_PRESET_KEYS:
            backup_val = st.session_state.get(f"backup_{key}")
            if backup_val is not None:
                st.session_state[key] = backup_val


class MockYTFile:
    def __init__(self, url):
        self.url = url
        clean_name = "youtube_video.mp4"
        if "watch?v=" in url:
            vid = url.split("watch?v=")[1].split("&")[0]
            clean_name = f"yt_{vid}.mp4"
        elif "youtu.be/" in url:
            vid = url.split("youtu.be/")[1].split("?")[0]
            clean_name = f"yt_{vid}.mp4"
        self.name = clean_name


def init_task_state(task_idx, default_config):
    """Ensure all style properties for batch task_idx are initialized in session state."""
    props = [
        ("username", default_config.card_username),
        ("handle", default_config.card_handle),
        ("bg_color", default_config.card_bg_color),
        ("text_color", default_config.card_text_color),
        ("border_color", default_config.card_border_color),
        ("border_width", default_config.card_border_width),
        ("radius", default_config.card_radius),
        ("avatar_text", default_config.card_avatar_text),
        ("avatar_bg", default_config.card_avatar_bg),
        ("avatar_path", default_config.card_avatar_path),
        ("font_username", default_config.card_font_username),
        ("font_handle", default_config.card_font_handle),
        ("font_body", default_config.card_font_body),
        ("width", default_config.card_width),
        ("height", default_config.card_height),
        ("font_size_body", default_config.card_font_size_body),
        ("y_position", default_config.card_y_position),
        ("avatar_radius", default_config.card_avatar_radius),
        ("font_size_username", default_config.card_font_size_username),
        ("font_size_handle", default_config.card_font_size_handle),
        ("video_zoom", default_config.video_zoom),
        ("video_y_position", default_config.video_y_position),
    ]
    for prop, default_val in props:
        key = f"batch_{prop}_{task_idx}"
        if key not in st.session_state:
            editor_key = prop if prop in ["video_zoom", "video_y_position"] else f"card_{prop}"
            st.session_state[key] = st.session_state.get(editor_key, default_val)


def get_task_style(task_idx, default_config, session_state):
    """Resolve card styling dictionary for a batch task."""
    init_task_state(task_idx, default_config)
    return {
        "card_username": session_state.get(f"batch_username_{task_idx}"),
        "card_handle": session_state.get(f"batch_handle_{task_idx}"),
        "card_bg_color": session_state.get(f"batch_bg_color_{task_idx}"),
        "card_text_color": session_state.get(f"batch_text_color_{task_idx}"),
        "card_border_color": session_state.get(f"batch_border_color_{task_idx}"),
        "card_border_width": session_state.get(f"batch_border_width_{task_idx}"),
        "card_radius": session_state.get(f"batch_radius_{task_idx}"),
        "card_avatar_text": session_state.get(f"batch_avatar_text_{task_idx}"),
        "card_avatar_bg": session_state.get(f"batch_avatar_bg_{task_idx}"),
        "card_avatar_path": session_state.get(f"batch_avatar_path_{task_idx}"),
        "card_font_username": session_state.get(f"batch_font_username_{task_idx}"),
        "card_font_handle": session_state.get(f"batch_font_handle_{task_idx}"),
        "card_font_body": session_state.get(f"batch_font_body_{task_idx}"),
        "card_width": session_state.get(f"batch_width_{task_idx}"),
        "card_height": session_state.get(f"batch_height_{task_idx}"),
        "card_font_size_body": session_state.get(f"batch_font_size_body_{task_idx}"),
        "card_y_position": session_state.get(f"batch_y_position_{task_idx}"),
        "card_avatar_radius": session_state.get(f"batch_avatar_radius_{task_idx}"),
        "card_font_size_username": session_state.get(f"batch_font_size_username_{task_idx}"),
        "card_font_size_handle": session_state.get(f"batch_font_size_handle_{task_idx}"),
        "video_zoom": session_state.get(f"batch_video_zoom_{task_idx}"),
        "video_y_position": session_state.get(f"batch_video_y_position_{task_idx}"),
    }


def main():
    inject_modern_custom_theme()
    default_config = load_initial_config()

    # Brand Title Header
    st.markdown('<div class="brand-header-title">⚡ AI Video Producer Pro</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="brand-header-sub">Plataforma Inteligente de Producción de Videos Promocionales para TikTok, Reels y Shorts</div>',
        unsafe_allow_html=True
    )

    # Initialize session state baseline keys
    if "video_zoom" not in st.session_state:
        st.session_state.video_zoom = default_config.video_zoom
    if "video_y_position" not in st.session_state:
        st.session_state.video_y_position = default_config.video_y_position
    if "card_username" not in st.session_state:
        st.session_state.card_username = default_config.card_username
        st.session_state.card_handle = default_config.card_handle
        st.session_state.card_bg_color = default_config.card_bg_color
        st.session_state.card_text_color = default_config.card_text_color
        st.session_state.card_border_color = default_config.card_border_color
        st.session_state.card_border_width = default_config.card_border_width
        st.session_state.card_radius = default_config.card_radius
        st.session_state.card_avatar_text = default_config.card_avatar_text
        st.session_state.card_avatar_bg = default_config.card_avatar_bg
        st.session_state.card_avatar_path = default_config.card_avatar_path
        st.session_state.card_font_username = default_config.card_font_username
        st.session_state.card_font_handle = default_config.card_font_handle
        st.session_state.card_font_body = default_config.card_font_body
        st.session_state.card_width = default_config.card_width
        st.session_state.card_height = default_config.card_height
        st.session_state.card_font_size_body = default_config.card_font_size_body
        st.session_state.card_y_position = default_config.card_y_position
        st.session_state.auto_cut = False
        st.session_state.card_text = "¡Este juego cooperativo de sigilo es brutal! Pasa del día calmado a la caza nocturna en segundos..."
        st.session_state.card_avatar_radius = default_config.card_avatar_radius
        st.session_state.card_font_size_username = default_config.card_font_size_username
        st.session_state.card_font_size_handle = default_config.card_font_size_handle

    # Preset setup
    presets_dict = get_predetermined_presets()
    preset_options = ["Personalizado (Editor Libre)"] + [f"Predeterminado: {name}" for name in presets_dict.keys()]
    
    selected_presets = []
    for opt in preset_options:
        if st.session_state.get(f"simple_cb_preset_{opt}"):
            selected_presets.append(opt)
    if not selected_presets:
        selected_presets = ["Personalizado (Editor Libre)"]
        
    preview_preset = st.session_state.get("simple_preview_preset_select", selected_presets[0]) if len(selected_presets) > 1 else selected_presets[0]
    sync_preview_state(preview_preset, presets_dict, default_config)

    # -------------------------------------------------------------------------
    # Sidebar Configuration Panel
    # -------------------------------------------------------------------------
    st.sidebar.markdown("### ⚙️ Configuración Global")
    
    # Scoped Format Selector (1080p vs 4K Vertical)
    format_option = st.sidebar.selectbox(
        "📐 Formato de Salida (9:16 Vertical)",
        options=[
            "9:16 Full HD (1080x1920)",
            "9:16 4K Ultra HD (2160x3840)"
        ],
        index=0 if (default_config.output_width == 1080 and default_config.output_height == 1920) else 1,
        help="Elige la resolución vertical para tu contenido."
    )
    if "4K" in format_option:
        output_width, output_height = 2160, 3840
    else:
        output_width, output_height = 1080, 1920

    video_style = "vertical_split_blur"  # Locked to Fondo borroso + Tarjeta
    is_card_style = True
    auto_cut = False
    st.session_state.auto_cut = False

    # API Keys Expander
    with st.sidebar.expander("🔑 Credenciales de API"):
        groq_key = st.text_input(
            "Groq API Key",
            value=default_config.groq_api_key,
            type="password",
            help="console.groq.com"
        )
        gemini_key = st.text_input(
            "Gemini API Key",
            value=default_config.gemini_api_key,
            type="password",
            help="aistudio.google.com"
        )
        eleven_key = st.text_input(
            "ElevenLabs API Key",
            value=default_config.elevenlabs_api_key,
            type="password",
            help="elevenlabs.io"
        )
        voice_id = default_config.elevenlabs_voice_id

    # Advanced Settings Expander
    with st.sidebar.expander("🛠️ Ajustes Avanzados de Render"):
        crf = st.slider(
            "Calidad de Video (CRF)",
            min_value=0,
            max_value=51,
            value=default_config.video_crf,
            help="18 es la calidad de producción óptima."
        )
        max_retries = st.number_input(
            "Reintentos Máximos de API",
            min_value=1,
            max_value=10,
            value=default_config.max_retries,
        )
        target_duration = default_config.target_duration

    # -------------------------------------------------------------------------
    # Main Navigation Tabs
    # -------------------------------------------------------------------------
    tab_feed, tab_editor, tab_batch, tab_benchmark = st.tabs([
        "🚀 Feed de Trailers (Pre-Producción)",
        "🎬 Modo Individual (Editor)",
        "📦 Super Batch (Procesador por Lote)",
        "⚡ Benchmark (Rendimiento)"
    ])

    # =========================================================================
    # TAB 1: Feed de Trailers (Pre-Producción & Database)
    # =========================================================================
    with tab_feed:
        st.markdown("### 🍿 Pre-Producción: Database & Feed de Trailers")
        st.markdown(
            "Los últimos trailers de **Indie Games Hub** y **GameTrailers (IGN)** se recopilan aquí automáticamente. "
            "Revisa los trailers y decide qué juegos pasan a producción enviándolos al Editor Individual o al Super Batch."
        )

        col_timeframe, col_btn_refresh, col_filter_ch, col_search, col_clear_db = st.columns([3, 3, 3, 3, 2])

        with col_timeframe:
            timeframe_label = st.selectbox(
                "⏱️ Periodo a Scrapear",
                options=["Últimas 24 horas", "Últimos 3 días", "Últimos 7 días"],
                index=0,
                key="feed_timeframe_select"
            )
            tf_hours = 24 if timeframe_label == "Últimas 24 horas" else (72 if timeframe_label == "Últimos 3 días" else 168)

        with col_btn_refresh:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            if st.button("🔄 Scrapear Trailers", key="btn_refresh_feed", use_container_width=True, help=f"Scrapear trailers subidos en {timeframe_label.lower()}"):
                with st.spinner(f"Scrapeando trailers subidos en {timeframe_label.lower()}..."):
                    try:
                        new_c, total_c = refresh_all_trailers(timeframe_hours=tf_hours)
                        st.toast(f"✅ ¡{new_c} trailers nuevos añadidos ({timeframe_label})! Total: {total_c}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al scrapear trailers: {e}")

        db = TrailerDatabaseManager()
        if not db.trailers:
            with st.spinner("Cargando base de datos inicial de trailers..."):
                refresh_all_trailers(timeframe_hours=24)
                db = TrailerDatabaseManager()

        with col_filter_ch:
            ch_filter = st.selectbox(
                "Filtrar Canal",
                options=["Todos", "Indie Games Hub", "GameTrailers (IGN)"],
                key="feed_ch_filter"
            )

        with col_search:
            search_query = st.text_input(
                "Buscar Juego",
                placeholder="🔍 Nombre del juego...",
                key="feed_search_input"
            )

        with col_clear_db:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            if st.button("🗑️ Vaciar Todo", key="btn_clear_entire_db", use_container_width=True, help="Eliminar todas las entradas de la base de datos de trailers"):
                db.clear_all()
                st.toast("🗑️ Base de datos vaciada por completo")
                st.rerun()

        feed_items = db.get_feed_items(channel_filter=ch_filter, search_query=search_query)

        if not feed_items:
            st.info("No se encontraron trailers activos en el feed. Haz clic en **🔄 Actualizar Feed de Trailers**.")
        else:
            st.caption(f"📊 Mostrando {len(feed_items)} registros en la base de datos de trailers")
            st.markdown("---")
            
            # Display items as database rows
            for item in feed_items:
                yt_id = item["id"]
                g_title = item["game_name"]
                orig_title = item["title"]
                ch_name = item["channel"]
                yt_url = item["url"]
                thumb_url = item["thumbnail"]
                scraped_time = item.get("scraped_at", "")[:10]
                badge_class = "badge-igd" if "Indie" in ch_name else "badge-guam"

                with st.container():
                    col_thumb, col_info, col_prev, col_act = st.columns([2, 5, 2, 3])
                    
                    with col_thumb:
                        st.image(thumb_url, use_container_width=True)

                    with col_info:
                        st.markdown(f"""
                        <div style="margin-bottom: 2px;">
                            <span class="{badge_class}">{ch_name}</span>
                            <span style="color: #6b7280; font-size: 0.75rem; font-weight: 600; margin-left: 8px;">ID: {yt_id} • {scraped_time}</span>
                        </div>
                        <h4 style="color: #ffffff; margin: 2px 0; font-size: 1.15rem; font-weight: 800;">🎮 {g_title}</h4>
                        <p style="color: #9ca3af; font-size: 0.82rem; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            {orig_title}
                        </p>
                        """, unsafe_allow_html=True)

                    with col_prev:
                        with st.expander("▶️ Ver Trailer"):
                            st.video(yt_url)

                    with col_act:
                        btn_c1, btn_c2, btn_c3 = st.columns([4, 4, 2])
                        with btn_c1:
                            if st.button("⚡ Editar", key=f"btn_edit_{yt_id}", use_container_width=True, help="Mandar a Editor Individual"):
                                st.session_state["game_name"] = g_title
                                st.session_state["yt_url_input"] = yt_url
                                st.session_state["input_source_single"] = "Descargar de YouTube (Trailer / Gameplay)"
                                preset_hint = item.get("preset_hint", "IGD")
                                st.session_state[f"simple_cb_preset_Predeterminado: {preset_hint}"] = True
                                
                                if groq_key or gemini_key:
                                    try:
                                        from utils import generate_hook_with_groq
                                        auto_hook = generate_hook_with_groq(g_title, groq_key, gemini_api_key=gemini_key)
                                        if auto_hook:
                                            st.session_state.card_text = auto_hook
                                    except Exception as e_h:
                                        logger.warning(f"Auto hook generation on feed edit: {e_h}")
                                        
                                st.toast(f"⚡ Juego '{g_title}' cargado. ¡Pasa a la pestaña '🎬 Modo Individual (Editor)'!")

                        with btn_c2:
                            if st.button("➕ Batch", key=f"btn_batch_{yt_id}", use_container_width=True, help="Añadir a la cola del Super Batch"):
                                if "queued_batch_items" not in st.session_state:
                                    st.session_state["queued_batch_items"] = []
                                
                                if not any(b["id"] == yt_id for b in st.session_state["queued_batch_items"]):
                                    st.session_state["queued_batch_items"].append(item)
                                    q_idx = len(st.session_state["queued_batch_items"]) - 1
                                    
                                    # Auto-assign to batch task slot immediately
                                    if q_idx < 10:
                                        st.session_state[f"batch_src_type_{q_idx}"] = "Trailer de YouTube"
                                        st.session_state[f"batch_game_{q_idx}"] = g_title
                                        st.session_state[f"batch_yt_url_{q_idx}"] = yt_url
                                        preset_hint = item.get("preset_hint", "IGD")
                                        st.session_state[f"batch_cb_preset_{q_idx}_Predeterminado: {preset_hint}"] = True
                                        
                                        # Auto-generate hook if API key available
                                        if (groq_key or gemini_key) and not st.session_state.get(f"batch_text_{q_idx}"):
                                            try:
                                                from utils import generate_hook_with_groq
                                                auto_h = generate_hook_with_groq(g_title, groq_key, gemini_api_key=gemini_key)
                                                if auto_h:
                                                    st.session_state[f"batch_text_{q_idx}"] = auto_h
                                            except Exception as e_bh:
                                                logger.warning(f"Batch auto hook error: {e_bh}")

                                    st.session_state["batch_num_tasks"] = max(1, min(10, len(st.session_state["queued_batch_items"])))
                                    st.toast(f"➕ ¡'{g_title}' añadido al Super Batch! (Tarea #{q_idx+1})")
                                else:
                                    st.toast(f"⚠️ '{g_title}' ya está en la cola del Super Batch")

                        with btn_c3:
                            if st.button("🗑️", key=f"btn_arch_{yt_id}", use_container_width=True, help="Archivar este trailer"):
                                db.set_status(yt_id, "archived")
                                st.toast(f"🗑️ Trailer de {g_title} archivado")
                                st.rerun()

                    st.markdown('<hr style="margin: 8px 0; border: none; border-top: 1px solid rgba(255, 255, 255, 0.06);" />', unsafe_allow_html=True)

    # =========================================================================
    # TAB 2: Modo Individual (Editor)
    # =========================================================================
    with tab_editor:
        col_left, col_right = st.columns([3, 2])

        with col_left:
            st.subheader("📥 Selección del Origen del Gameplay")
            input_source = st.radio(
                "Origen del Gameplay",
                options=["Descargar de YouTube (Trailer / Gameplay)", "Subir archivo local (.mp4)"],
                horizontal=True,
                key="input_source_single",
                label_visibility="collapsed"
            )

            uploaded_files = []
            yt_url = ""
            yt_start = 0.0
            yt_end = 30.0

            if input_source == "Subir archivo local (.mp4)":
                st.subheader("📥 Subir Video Local")
                uploaded_files_raw = st.file_uploader(
                    "Arrastra un video de gameplay (MP4)",
                    type=["mp4"],
                    accept_multiple_files=False,
                    key="single_file_uploader"
                )
                if uploaded_files_raw:
                    uploaded_files = [uploaded_files_raw]
            else:
                st.subheader("🔗 Trailer de YouTube")
                yt_url = st.text_input(
                    "Enlace del video/trailer de YouTube",
                    placeholder="https://www.youtube.com/watch?v=...",
                    key="yt_url_input"
                )
                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    yt_start = st.number_input("Segundo de inicio (ss)", min_value=0.0, value=0.0, step=1.0, key="single_yt_start")
                with col_t2:
                    yt_end = st.number_input("Segundo de fin (to)", min_value=1.0, value=30.0, step=1.0, key="single_yt_end")
                
                if yt_url:
                    uploaded_files = [MockYTFile(yt_url)]

            if uploaded_files:
                if input_source == "Subir archivo local (.mp4)":
                    st.info(f"Video seleccionado: **{uploaded_files[0].name}** ({uploaded_files[0].size / (1024*1024):.1f} MB)")
                else:
                    st.info(f"🎥 Se descargará de YouTube: `{yt_url}` (segundos {yt_start} a {yt_end})")

                export_filename = st.text_input(
                    "Nombre del archivo final",
                    value=f"edit_{Path(uploaded_files[0].name).stem}.mp4",
                    key="single_export_filename"
                )

                # Card customizer expander
                with st.expander("🎨 Diseñar y Personalizar Tarjeta de Gancho", expanded=True):
                    st.markdown("Selecciona el preset o personaliza el estilo visual de la tarjeta.")
                    
                    st.markdown("**Selecciona los presets/cuentas para exportar:**")
                    selected_presets = []
                    cols_cb = st.columns(len(preset_options))
                    for idx, opt in enumerate(preset_options):
                        with cols_cb[idx]:
                            checked = st.checkbox(
                                opt,
                                value=(opt == "Personalizado (Editor Libre)"),
                                key=f"simple_cb_preset_{opt}",
                                help=f"Exportar con '{opt}'"
                            )
                            if checked:
                                selected_presets.append(opt)

                    if not selected_presets:
                        selected_presets = ["Personalizado (Editor Libre)"]

                    preview_preset = st.selectbox(
                        "👁️ Ver Vista Previa de:",
                        options=selected_presets,
                        key="simple_preview_preset_select"
                    ) if len(selected_presets) > 1 else selected_presets[0]

                    is_predetermined = preview_preset.startswith("Predeterminado: ")

                    if not is_predetermined:
                        col_preset1, col_preset2 = st.columns(2)
                        with col_preset1:
                            preset_data = export_card_preset(st.session_state)
                            st.download_button(
                                "💾 Descargar Preset",
                                data=json.dumps(preset_data, indent=2, ensure_ascii=False),
                                file_name="card_preset.json",
                                mime="application/json",
                                key="simple_preset_download"
                            )
                        with col_preset2:
                            st.file_uploader(
                                "📂 Cargar Preset",
                                type=["json"],
                                key="preset_uploader",
                                label_visibility="collapsed"
                            )

                    col_e1, col_e2 = st.columns([5, 6])
                    with col_e1:
                        st.text_input(
                            "🎮 Nombre del Juego (para buscar en Steam)",
                            key="game_name",
                            help="Escribe el nombre del juego para redactar el gancho con Groq/Gemini."
                        )

                        def _cb_single_hook_action(act="generate"):
                            g_name = st.session_state.get("game_name", "").strip()
                            if act == "generate":
                                if not groq_key and not gemini_key:
                                    st.session_state["single_hook_err"] = "❌ Escribe la Groq/Gemini API Key en la barra lateral."
                                    return
                                if not g_name:
                                    st.session_state["single_hook_err"] = "⚠️ Escribe el nombre del juego primero."
                                    return
                                try:
                                    from utils import generate_hook_with_groq
                                    h_gen = generate_hook_with_groq(g_name, groq_key, gemini_api_key=gemini_key)
                                    if h_gen:
                                        st.session_state.card_text = h_gen
                                        st.session_state["single_hook_err"] = None
                                except Exception as e_h:
                                    st.session_state["single_hook_err"] = f"Error: {e_h}"
                            else:
                                curr_t = st.session_state.get("card_text", "").strip()
                                if not curr_t:
                                    st.session_state["single_hook_err"] = "⚠️ Escribe o genera un gancho primero."
                                    return
                                if not groq_key and not gemini_key:
                                    st.session_state["single_hook_err"] = "❌ Escribe la API Key en la barra lateral."
                                    return
                                try:
                                    from utils import modify_hook_length
                                    mod_t = modify_hook_length(curr_t, act, groq_key, gemini_api_key=gemini_key)
                                    if mod_t:
                                        st.session_state.card_text = mod_t
                                        st.session_state["single_hook_err"] = None
                                except Exception as e_m:
                                    st.session_state["single_hook_err"] = f"Error: {e_m}"

                        st.button(
                            "🪄 Generar Gancho con IA",
                            key="single_gen_btn",
                            use_container_width=True,
                            on_click=_cb_single_hook_action,
                            kwargs={"act": "generate"}
                        )

                        st.text_area(
                            "Texto de la Tarjeta (Gancho)",
                            key="card_text",
                            help="Edita el gancho de tu video."
                        )

                        col_s_short, col_s_long = st.columns(2)
                        with col_s_short:
                            st.button(
                                "✂️ Shorter",
                                key="single_shorter_btn",
                                use_container_width=True,
                                on_click=_cb_single_hook_action,
                                kwargs={"act": "shorter"}
                            )
                        with col_s_long:
                            st.button(
                                "📝 Longer",
                                key="single_longer_btn",
                                use_container_width=True,
                                on_click=_cb_single_hook_action,
                                kwargs={"act": "longer"}
                            )

                        if st.session_state.get("single_hook_err"):
                            st.error(st.session_state["single_hook_err"])

                        if not is_predetermined:
                            st.text_input("Nombre de Usuario", key="card_username")
                            st.text_input("Handle (@)", key="card_handle")

                    with col_e2:
                        video_zoom = st.slider(
                            "🔍 Zoom Gameplay",
                            min_value=1.0,
                            max_value=3.0,
                            value=float(st.session_state.get("video_zoom", 1.0)),
                            step=0.05,
                            key="video_zoom"
                        )
                        video_y_position = st.slider(
                            "↕️ Posición Y Gameplay (px)",
                            min_value=-1000,
                            max_value=1000,
                            value=int(st.session_state.get("video_y_position", 0)),
                            step=10,
                            key="video_y_position"
                        )

                        st.slider(
                            "Posición Y Etiqueta (px)",
                            min_value=100,
                            max_value=3000,
                            step=50,
                            key="card_y_position",
                            disabled=is_predetermined
                        )
                        st.slider(
                            "Tamaño Texto (px)",
                            min_value=24,
                            max_value=128,
                            step=2,
                            key="card_font_size_body"
                        )

                        if not is_predetermined:
                            st.slider("Ancho Etiqueta (px)", min_value=1000, max_value=2100, step=50, key="card_width")
                            st.slider("Alto Etiqueta (px)", min_value=300, max_value=1200, step=20, key="card_height")
                            st.slider("Tamaño Avatar (px)", min_value=30, max_value=150, step=5, key="card_avatar_radius")
                            st.slider("Tamaño ID (px)", min_value=20, max_value=80, step=2, key="card_font_size_username")
                            st.slider("Tamaño Arroba (px)", min_value=16, max_value=70, step=2, key="card_font_size_handle")

            # Single processing execution button
            run_button = st.button("🚀 Iniciar Procesamiento (Modo Individual)", type="primary", use_container_width=True)

        with col_right:
            st.subheader("👁️ Previsualización del Diseño")
            preview_card_path = Path("temp") / "preview_card_temp.png"
            preview_mockup_path = Path("temp") / "preview_mockup_temp.png"
            
            from utils import create_text_card, create_video_preview
            try:
                create_text_card(
                    text=st.session_state.card_text,
                    output_path=preview_card_path,
                    username=st.session_state.card_username,
                    handle=st.session_state.card_handle,
                    bg_color=st.session_state.card_bg_color,
                    text_color=st.session_state.card_text_color,
                    border_color=st.session_state.card_border_color,
                    border_width=st.session_state.card_border_width,
                    radius=st.session_state.card_radius,
                    avatar_text=st.session_state.card_avatar_text,
                    avatar_bg=st.session_state.card_avatar_bg,
                    avatar_image_path=st.session_state.card_avatar_path,
                    font_username_family=st.session_state.card_font_username,
                    font_handle_family=st.session_state.card_font_handle,
                    font_body_family=st.session_state.card_font_body,
                    width=st.session_state.card_width,
                    height=st.session_state.card_height,
                    font_size_body=st.session_state.card_font_size_body,
                    avatar_radius=st.session_state.card_avatar_radius,
                    font_size_username=st.session_state.card_font_size_username,
                    font_size_handle=st.session_state.card_font_size_handle,
                )

                create_video_preview(
                    card_image_path=preview_card_path,
                    output_preview_path=preview_mockup_path,
                    style=video_style,
                    username=st.session_state.card_username,
                    card_y_position=st.session_state.card_y_position,
                    zoom=st.session_state.video_zoom,
                    video_y_position=st.session_state.video_y_position,
                )

                if preview_mockup_path.exists():
                    st.image(str(preview_mockup_path), caption="Simulación del Video Final", use_container_width=True)
            except Exception as e:
                st.error(f"Error generando vista previa: {e}")

            if run_button and uploaded_files:
                st.markdown("---")
                st.subheader("⏳ Progreso de Ejecución")
                
                # Single execution processing logic
                for f_idx, current_file in enumerate(uploaded_files):
                    st.markdown(f"### 📹 Procesando Video ({f_idx+1}/{len(uploaded_files)}): `{current_file.name}`")
                    
                    input_dir = Path("input")
                    input_dir.mkdir(exist_ok=True)
                    from utils import generate_run_id
                    single_run_id = generate_run_id()

                    if isinstance(current_file, MockYTFile):
                        input_video_path = input_dir / f"single_yt_clip_{single_run_id}.mp4"
                        with st.spinner("Descargando segmento de YouTube..."):
                            from utils import download_youtube_clip
                            download_youtube_clip(
                                url=current_file.url,
                                start_sec=yt_start,
                                end_sec=yt_end,
                                output_path=input_video_path,
                                ffmpeg_bin=default_config.ffmpeg_path
                            )
                    else:
                        input_video_path = input_dir / f"{single_run_id}_{current_file.name}"
                        with open(input_video_path, "wb") as f:
                            f.write(current_file.getbuffer())

                    styles_list = []
                    for p in selected_presets:
                        if p == "Personalizado (Editor Libre)":
                            styles_list.append({
                                "name": "Personalizado",
                                "card_username": st.session_state.card_username,
                                "card_handle": st.session_state.card_handle,
                                "card_bg_color": st.session_state.card_bg_color,
                                "card_text_color": st.session_state.card_text_color,
                                "card_border_color": st.session_state.card_border_color,
                                "card_border_width": st.session_state.card_border_width,
                                "card_radius": st.session_state.card_radius,
                                "card_avatar_text": st.session_state.card_avatar_text,
                                "card_avatar_bg": st.session_state.card_avatar_bg,
                                "card_avatar_path": st.session_state.card_avatar_path,
                                "card_font_username": st.session_state.card_font_username,
                                "card_font_handle": st.session_state.card_font_handle,
                                "card_font_body": st.session_state.card_font_body,
                                "card_width": st.session_state.card_width,
                                "card_height": st.session_state.card_height,
                                "card_font_size_body": st.session_state.card_font_size_body,
                                "card_y_position": st.session_state.card_y_position,
                                "card_avatar_radius": st.session_state.card_avatar_radius,
                                "card_font_size_username": st.session_state.card_font_size_username,
                                "card_font_size_handle": st.session_state.card_font_size_handle,
                                "video_zoom": st.session_state.video_zoom,
                                "video_y_position": st.session_state.video_y_position,
                            })
                        else:
                            p_name = p.replace("Predeterminado: ", "")
                            p_info = presets_dict.get(p_name)
                            if p_info:
                                with open(p_info["json_path"], "r", encoding="utf-8") as f_p:
                                    p_data = json.load(f_p)
                                p_data["name"] = p_name
                                if p_info["image_path"]:
                                    p_data["card_avatar_path"] = str(p_info["image_path"])
                                p_data["card_font_size_body"] = st.session_state.card_font_size_body
                                styles_list.append(p_data)

                    first_s = styles_list[0]
                    app_config = AppConfig(
                        gemini_api_key=gemini_key,
                        gemini_model="gemini-3.5-flash",
                        elevenlabs_api_key=eleven_key,
                        elevenlabs_voice_id=voice_id,
                        target_duration=target_duration,
                        output_width=output_width,
                        output_height=output_height,
                        video_style=video_style,
                        video_zoom=first_s.get("video_zoom", default_config.video_zoom),
                        video_y_position=first_s.get("video_y_position", default_config.video_y_position),
                        video_crf=crf,
                        max_retries=max_retries,
                        card_username=first_s.get("card_username", default_config.card_username),
                        card_handle=first_s.get("card_handle", default_config.card_handle),
                        card_bg_color=first_s.get("card_bg_color", default_config.card_bg_color),
                        card_text_color=first_s.get("card_text_color", default_config.card_text_color),
                        card_border_color=first_s.get("card_border_color", default_config.card_border_color),
                        card_border_width=first_s.get("card_border_width", default_config.card_border_width),
                        card_radius=first_s.get("card_radius", default_config.card_radius),
                        card_avatar_text=first_s.get("card_avatar_text", default_config.card_avatar_text),
                        card_avatar_bg=first_s.get("card_avatar_bg", default_config.card_avatar_bg),
                        card_avatar_path=first_s.get("card_avatar_path", default_config.card_avatar_path),
                        card_font_username=first_s.get("card_font_username", default_config.card_font_username),
                        card_font_handle=first_s.get("card_font_handle", default_config.card_font_handle),
                        card_font_body=first_s.get("card_font_body", default_config.card_font_body),
                        card_width=first_s.get("card_width", default_config.card_width),
                        card_height=first_s.get("card_height", default_config.card_height),
                        card_font_size_body=first_s.get("card_font_size_body", default_config.card_font_size_body),
                        card_y_position=first_s.get("card_y_position", default_config.card_y_position),
                        auto_cut=False,
                        card_text=st.session_state.card_text,
                        card_avatar_radius=first_s.get("card_avatar_radius", default_config.card_avatar_radius),
                        card_font_size_username=first_s.get("card_font_size_username", default_config.card_font_size_username),
                        card_font_size_handle=first_s.get("card_font_size_handle", default_config.card_font_size_handle),
                        game_name=st.session_state.get("game_name", ""),
                        groq_api_key=groq_key,
                    )

                    setup_logging(app_config.log_level, app_config.logs_dir)
                    steps = ["Generación de Tarjeta", "Composición de Diseño y Exportación Final"]
                    placeholders = [st.empty() for _ in steps]
                    for idx, step in enumerate(steps):
                        placeholders[idx].markdown(f"⏳ **Paso {idx+1}: {step}** — *Esperando...*")

                    try:
                        pipeline = Pipeline(app_config)
                        from schemas import PipelineContext
                        ctx = PipelineContext(run_id=single_run_id, input_video=input_video_path)

                        t0 = time.time()
                        placeholders[0].markdown(f"🔄 **Paso 1: {steps[0]}** — *Generando...*")
                        ctx.merged_video_path = ctx.input_video
                        placeholders[0].markdown(f"✅ **Paso 1: {steps[0]}** — *¡Completado! ({time.time()-t0:.1f}s)*")

                        t0 = time.time()
                        placeholders[1].markdown(f"🔄 **Paso 2: {steps[1]}** — *Renderizando FFmpeg...*")
                        final_res = pipeline.export(ctx, export_filename, styles_list=styles_list)
                        placeholders[1].markdown(f"✅ **Paso 2: {steps[1]}** — *¡Completado! ({time.time()-t0:.1f}s)*")

                        st.success("🎉 ¡Video procesado con éxito!")
                        if isinstance(final_res, list):
                            for res_p in final_res:
                                st.video(str(res_p))
                        else:
                            st.video(str(final_res))
                    except Exception as e_run:
                        st.error(f"Error procesando video: {e_run}")

    # =========================================================================
    # TAB 3: Super Batch (Procesador por Lote)
    # =========================================================================
    with tab_batch:
        st.subheader("📦 Super Batch Configurator (1-10 Videos)")
        
        queued_from_feed = st.session_state.get("queued_batch_items", [])
        if queued_from_feed:
            with st.expander(f"📋 Cola de Producción en Lote ({len(queued_from_feed)} juegos asignados)", expanded=True):
                col_q_info, col_q_clear = st.columns([8, 2])
                with col_q_info:
                    st.caption("Los trailers enviados desde el Feed están asignados automáticamente a las tareas del Super Batch.")
                with col_q_clear:
                    if st.button("🗑️ Vaciar Cola", key="btn_clear_batch_queue", use_container_width=True):
                        st.session_state["queued_batch_items"] = []
                        st.session_state["batch_num_tasks"] = 1
                        st.toast("🗑️ Cola del Super Batch vaciada")
                        st.rerun()

                for q_idx, q_item in enumerate(queued_from_feed):
                    q_c1, q_c2, q_c3 = st.columns([1, 8, 2])
                    with q_c1:
                        st.image(q_item["thumbnail"], width=60)
                    with q_c2:
                        st.markdown(f"**Tarea #{q_idx+1}: {q_item['game_name']}** (`{q_item['channel']}`) — *{q_item['title']}*")
                    with q_c3:
                        if st.button("❌ Quitar", key=f"btn_rem_batch_{q_item['id']}", use_container_width=True):
                            st.session_state["queued_batch_items"].pop(q_idx)
                            # Re-assign remaining items to task slots
                            for new_i, item_rem in enumerate(st.session_state["queued_batch_items"]):
                                st.session_state[f"batch_src_type_{new_i}"] = "Trailer de YouTube"
                                st.session_state[f"batch_game_{new_i}"] = item_rem["game_name"]
                                st.session_state[f"batch_yt_url_{new_i}"] = item_rem["url"]
                            st.session_state["batch_num_tasks"] = max(1, len(st.session_state["queued_batch_items"]))
                            st.toast(f"❌ '{q_item['game_name']}' quitado del Super Batch")
                            st.rerun()

        num_tasks = st.number_input(
            "Número de Tareas en Lote",
            min_value=1,
            max_value=10,
            value=st.session_state.get("batch_num_tasks", 3),
            step=1,
            key="batch_num_tasks"
        )

        for i in range(num_tasks):
            with st.expander(f"🎬 Tarea #{i+1} - Ajustes Individuales", expanded=(i == 0)):
                col_inputs, col_preview = st.columns([5, 3])

                with col_inputs:
                    col_t_vid, col_t_txt, col_t_sty = st.columns(3)

                    with col_t_vid:
                        st.markdown("**1. Origen del Video**")
                        src_type = st.radio(
                            f"Origen del Gameplay #{i+1}",
                            options=["Trailer de YouTube", "Subir archivo local (.mp4)"],
                            horizontal=True,
                            key=f"batch_src_type_{i}",
                            label_visibility="collapsed"
                        )

                        if src_type == "Subir archivo local (.mp4)":
                            st.file_uploader(
                                f"Subir gameplay local (Tarea #{i+1})",
                                type=["mp4"],
                                key=f"batch_file_{i}"
                            )
                        else:
                            st.text_input(
                                f"Enlace de YouTube (Tarea #{i+1})",
                                placeholder="https://www.youtube.com/watch?v=...",
                                key=f"batch_yt_url_{i}"
                            )
                            col_ts1, col_ts2 = st.columns(2)
                            with col_ts1:
                                st.number_input(f"Inicio ss #{i+1}", min_value=0.0, value=0.0, step=1.0, key=f"batch_yt_start_{i}")
                            with col_ts2:
                                st.number_input(f"Fin to #{i+1}", min_value=1.0, value=30.0, step=1.0, key=f"batch_yt_end_{i}")

                    with col_t_txt:
                        st.markdown("**2. Texto / Gancho (IA)**")
                        st.text_input(
                            f"🎮 Nombre del Juego #{i+1}",
                            key=f"batch_game_{i}",
                            help="Escribe el nombre del juego para redactar el gancho."
                        )

                        def _cb_batch_hook_action(task_idx=i, act="generate"):
                            if act == "generate":
                                g_name = st.session_state.get(f"batch_game_{task_idx}", "").strip()
                                if not groq_key and not gemini_key:
                                    st.session_state[f"batch_err_{task_idx}"] = "❌ Escribe la API Key en la barra lateral."
                                    return
                                if not g_name:
                                    st.session_state[f"batch_err_{task_idx}"] = "⚠️ Escribe el nombre del juego primero."
                                    return
                                try:
                                    from utils import generate_hook_with_groq
                                    h_res = generate_hook_with_groq(g_name, groq_key, gemini_api_key=gemini_key)
                                    if h_res:
                                        st.session_state[f"batch_text_{task_idx}"] = h_res
                                        st.session_state[f"batch_err_{task_idx}"] = None
                                except Exception as e_gen:
                                    st.session_state[f"batch_err_{task_idx}"] = f"Error: {e_gen}"
                            else:
                                c_text = st.session_state.get(f"batch_text_{task_idx}", "").strip()
                                if not c_text:
                                    st.session_state[f"batch_err_{task_idx}"] = "⚠️ Escribe o genera un gancho primero."
                                    return
                                if not groq_key and not gemini_key:
                                    st.session_state[f"batch_err_{task_idx}"] = "❌ Introduce la API Key en la barra lateral."
                                    return
                                try:
                                    from utils import modify_hook_length
                                    m_text = modify_hook_length(c_text, act, groq_key, gemini_api_key=gemini_key)
                                    if m_text:
                                        st.session_state[f"batch_text_{task_idx}"] = m_text
                                        st.session_state[f"batch_err_{task_idx}"] = None
                                except Exception as e_mod:
                                    st.session_state[f"batch_err_{task_idx}"] = f"Error: {e_mod}"

                        st.button(
                            f"🪄 Generar Gancho #{i+1}",
                            key=f"batch_gen_btn_{i}",
                            use_container_width=True,
                            on_click=_cb_batch_hook_action,
                            kwargs={"task_idx": i, "act": "generate"}
                        )

                        st.text_area(
                            f"Texto de la Tarjeta #{i+1}",
                            key=f"batch_text_{i}",
                            help="Gancho final que aparecerá en este video exacto."
                        )

                        col_shorter, col_longer = st.columns(2)
                        with col_shorter:
                            st.button(
                                f"✂️ Shorter #{i+1}",
                                key=f"batch_shorter_btn_{i}",
                                use_container_width=True,
                                on_click=_cb_batch_hook_action,
                                kwargs={"task_idx": i, "act": "shorter"}
                            )
                        with col_longer:
                            st.button(
                                f"📝 Longer #{i+1}",
                                key=f"batch_longer_btn_{i}",
                                use_container_width=True,
                                on_click=_cb_batch_hook_action,
                                kwargs={"task_idx": i, "act": "longer"}
                            )

                        if st.session_state.get(f"batch_err_{i}"):
                            st.error(st.session_state[f"batch_err_{i}"])

                    with col_t_sty:
                        st.markdown("**3. Estética / Estilo de Tarjeta**")
                        selected_presets_batch = []
                        with st.expander("Seleccionar Diseños", expanded=True):
                            for opt in preset_options:
                                checked = st.checkbox(
                                    opt,
                                    value=(opt == "Personalizado (Editor Libre)"),
                                    key=f"batch_cb_preset_{i}_{opt}"
                                )
                                if checked:
                                    selected_presets_batch.append(opt)

                        if not selected_presets_batch:
                            selected_presets_batch = ["Personalizado (Editor Libre)"]

                        for opt in selected_presets_batch:
                            if opt.startswith("Predeterminado: "):
                                p_name = opt.replace("Predeterminado: ", "")
                                p_info = presets_dict.get(p_name)
                                p_font_size = 80
                                if p_info:
                                    try:
                                        with open(p_info["json_path"], "r", encoding="utf-8") as f_p:
                                            p_data = json.load(f_p)
                                        p_font_size = p_data.get("card_font_size_body", 80)
                                    except Exception:
                                        pass
                                st.slider(
                                    f"Tamaño Texto para {p_name} (px)",
                                    min_value=24,
                                    max_value=128,
                                    step=2,
                                    value=int(st.session_state.get(f"batch_font_size_body_{i}_{p_name}", p_font_size)),
                                    key=f"batch_font_size_body_{i}_{p_name}"
                                )

                with col_preview:
                    st.markdown(f"**👁️ Vista Previa Tarea #{i+1}**")
                    first_preset = selected_presets_batch[0]
                    style_params = get_task_style(i, default_config, st.session_state)
                    if first_preset.startswith("Predeterminado: "):
                        preset_name = first_preset.replace("Predeterminado: ", "")
                        preset_info = presets_dict.get(preset_name)
                        if preset_info:
                            try:
                                with open(preset_info["json_path"], "r", encoding="utf-8") as f_preset:
                                    data = json.load(f_preset)
                                for k, v in data.items():
                                    style_params[k] = v
                                style_params["name"] = preset_name
                                if preset_info["image_path"]:
                                    style_params["card_avatar_path"] = str(preset_info["image_path"])
                                style_params["card_font_size_body"] = st.session_state.get(f"batch_font_size_body_{i}_{preset_name}", style_params.get("card_font_size_body", 80))
                            except Exception as e_prv:
                                logger.error(f"Failed preset load for preview: {e_prv}")

                    t_text = st.session_state.get(f"batch_text_{i}", "¡Gancho de prueba para esta Tarea!")
                    preview_card_path_i = Path("temp") / f"preview_card_task_{i}.png"
                    preview_mockup_path_i = Path("temp") / f"preview_mockup_task_{i}.png"

                    from utils import create_text_card, create_video_preview
                    try:
                        create_text_card(
                            text=t_text,
                            output_path=preview_card_path_i,
                            username=style_params["card_username"],
                            handle=style_params["card_handle"],
                            bg_color=style_params["card_bg_color"],
                            text_color=style_params["card_text_color"],
                            border_color=style_params["card_border_color"],
                            border_width=style_params["card_border_width"],
                            radius=style_params["card_radius"],
                            avatar_text=style_params["card_avatar_text"],
                            avatar_bg=style_params["card_avatar_bg"],
                            avatar_image_path=style_params["card_avatar_path"],
                            font_username_family=style_params["card_font_username"],
                            font_handle_family=style_params["card_font_handle"],
                            font_body_family=style_params["card_font_body"],
                            width=style_params["card_width"],
                            height=style_params["card_height"],
                            font_size_body=style_params["card_font_size_body"],
                            avatar_radius=style_params["card_avatar_radius"],
                            font_size_username=style_params["card_font_size_username"],
                            font_size_handle=style_params["card_font_size_handle"],
                        )
                        create_video_preview(
                            card_image_path=preview_card_path_i,
                            output_preview_path=preview_mockup_path_i,
                            style=video_style,
                            username=style_params["card_username"],
                            card_y_position=style_params["card_y_position"],
                            zoom=style_params.get("video_zoom", 1.0),
                            video_y_position=style_params.get("video_y_position", 0),
                        )
                        if preview_mockup_path_i.exists():
                            st.image(str(preview_mockup_path_i), caption=f"Simulación Tarea #{i+1}", use_container_width=True)
                    except Exception as e_sim:
                        st.error(f"Vista previa #{i+1}: {e_sim}")

        st.markdown("---")
        batch_run_btn = st.button("🚀 Iniciar Super Batch (Procesar Tareas)", key="batch_run_btn", type="primary", use_container_width=True)

        if batch_run_btn:
            errors = []
            for i in range(num_tasks):
                src_t = st.session_state.get(f"batch_src_type_{i}")
                if src_t == "Subir archivo local (.mp4)":
                    if not st.session_state.get(f"batch_file_{i}"):
                        errors.append(f"❌ Tarea #{i+1}: Falta subir el archivo de video local.")
                else:
                    if not st.session_state.get(f"batch_yt_url_{i}"):
                        errors.append(f"❌ Tarea #{i+1}: Falta el enlace de YouTube.")

            if errors:
                for err in errors:
                    st.error(err)
            else:
                completed_batch_videos = []
                for i in range(num_tasks):
                    st.markdown(f"---\n### 📹 Ejecutando Tarea #{i+1}/{num_tasks}")
                    try:
                        src_t = st.session_state.get(f"batch_src_type_{i}")
                        g_name = st.session_state.get(f"batch_game_{i}", "")
                        t_text = st.session_state.get(f"batch_text_{i}", "")

                        input_dir = Path("input")
                        input_dir.mkdir(exist_ok=True)
                        from utils import generate_run_id
                        task_run_id = generate_run_id()

                        if src_t == "Subir archivo local (.mp4)":
                            u_file = st.session_state.get(f"batch_file_{i}")
                            input_video_path = input_dir / f"task_{i}_{u_file.name}"
                            with open(input_video_path, "wb") as f:
                                f.write(u_file.getbuffer())
                            export_name = f"edit_task_{i}_{Path(u_file.name).stem}.mp4"
                        else:
                            yt_url_task = st.session_state.get(f"batch_yt_url_{i}")
                            yt_start_task = st.session_state.get(f"batch_yt_start_{i}", 0.0)
                            yt_end_task = st.session_state.get(f"batch_yt_end_{i}", 30.0)
                            input_video_path = input_dir / f"task_{i}_yt_clip.mp4"
                            with st.spinner(f"Descargando segmento de YouTube para Tarea #{i+1}..."):
                                from utils import download_youtube_clip
                                download_youtube_clip(
                                    url=yt_url_task,
                                    start_sec=yt_start_task,
                                    end_sec=yt_end_task,
                                    output_path=input_video_path,
                                    ffmpeg_bin=default_config.ffmpeg_path
                                )
                            export_name = f"edit_task_{i}_youtube.mp4"

                        if g_name:
                            game_slug = "".join(g_name.split())
                            game_slug = re.sub(r'[\\/*?:"<>|]', "", game_slug)
                            if game_slug:
                                export_name = f"{game_slug}.mp4"

                        task_styles_list = []
                        for p in selected_presets_batch:
                            if p == "Personalizado (Editor Libre)":
                                style_params = get_task_style(i, default_config, st.session_state)
                                style_params["name"] = "Personalizado"
                                task_styles_list.append(style_params)
                            else:
                                preset_name = p.replace("Predeterminado: ", "")
                                preset_info = presets_dict.get(preset_name)
                                if preset_info:
                                    try:
                                        with open(preset_info["json_path"], "r", encoding="utf-8") as f_preset:
                                            data = json.load(f_preset)
                                        data["name"] = preset_name
                                        if preset_info["image_path"]:
                                            data["card_avatar_path"] = str(preset_info["image_path"])
                                        data["card_font_size_body"] = st.session_state.get(f"batch_font_size_body_{i}_{preset_name}", data.get("card_font_size_body", 80))
                                        task_styles_list.append(data)
                                    except Exception as e_l:
                                        logger.error(f"Failed preset load for task {i}: {e_l}")

                        first_style = task_styles_list[0]
                        app_config = AppConfig(
                            gemini_api_key=gemini_key,
                            gemini_model="gemini-3.5-flash",
                            elevenlabs_api_key=eleven_key,
                            elevenlabs_voice_id=voice_id,
                            target_duration=target_duration,
                            output_width=output_width,
                            output_height=output_height,
                            video_style=video_style,
                            video_zoom=first_style.get("video_zoom", default_config.video_zoom),
                            video_y_position=first_style.get("video_y_position", default_config.video_y_position),
                            video_crf=crf,
                            max_retries=max_retries,
                            card_username=first_style.get("card_username", default_config.card_username),
                            card_handle=first_style.get("card_handle", default_config.card_handle),
                            card_bg_color=first_style.get("card_bg_color", default_config.card_bg_color),
                            card_text_color=first_style.get("card_text_color", default_config.card_text_color),
                            card_border_color=first_style.get("card_border_color", default_config.card_border_color),
                            card_border_width=first_style.get("card_border_width", default_config.card_border_width),
                            card_radius=first_style.get("card_radius", default_config.card_radius),
                            card_avatar_text=first_style.get("card_avatar_text", default_config.card_avatar_text),
                            card_avatar_bg=first_style.get("card_avatar_bg", default_config.card_avatar_bg),
                            card_avatar_path=first_style.get("card_avatar_path", default_config.card_avatar_path),
                            card_font_username=first_style.get("card_font_username", default_config.card_font_username),
                            card_font_handle=first_style.get("card_font_handle", default_config.card_font_handle),
                            card_font_body=first_style.get("card_font_body", default_config.card_font_body),
                            card_width=first_style.get("card_width", default_config.card_width),
                            card_height=first_style.get("card_height", default_config.card_height),
                            card_font_size_body=first_style.get("card_font_size_body", default_config.card_font_size_body),
                            card_y_position=first_style.get("card_y_position", default_config.card_y_position),
                            auto_cut=False,
                            card_text=t_text,
                            card_avatar_radius=first_style.get("card_avatar_radius", default_config.card_avatar_radius),
                            card_font_size_username=first_style.get("card_font_size_username", default_config.card_font_size_username),
                            card_font_size_handle=first_style.get("card_font_size_handle", default_config.card_font_size_handle),
                            game_name=g_name,
                            groq_api_key=groq_key,
                        )

                        setup_logging(app_config.log_level, app_config.logs_dir)
                        steps = ["Generación de Tarjeta", "Composición y Exportación FFmpeg"]
                        placeholders = [st.empty() for _ in steps]
                        for idx, step in enumerate(steps):
                            placeholders[idx].markdown(f"⏳ **Paso {idx+1}: {step}** — *Esperando...*")

                        pipeline = Pipeline(app_config)
                        from schemas import PipelineContext
                        ctx = PipelineContext(run_id=task_run_id, input_video=input_video_path)

                        t0 = time.time()
                        placeholders[0].markdown(f"🔄 **Paso 1: {steps[0]}** — *Generando tarjeta...*")
                        ctx.merged_video_path = ctx.input_video
                        placeholders[0].markdown(f"✅ **Paso 1: {steps[0]}** — *¡Completado! ({time.time()-t0:.1f}s)*")

                        t0 = time.time()
                        placeholders[1].markdown(f"🔄 **Paso 2: {steps[1]}** — *FFmpeg formateando...*")
                        final_res = pipeline.export(ctx, export_name, styles_list=task_styles_list)
                        placeholders[1].markdown(f"✅ **Paso 2: {steps[1]}** — *¡Completado! ({time.time()-t0:.1f}s)*")

                        if isinstance(final_res, list):
                            for res_path in final_res:
                                completed_batch_videos.append((f"Tarea #{i+1}", res_path))
                            st.success(f"🎉 Tarea #{i+1} completada con éxito ({len(final_res)} vídeos).")
                        else:
                            completed_batch_videos.append((f"Tarea #{i+1}", final_res))
                            st.success(f"🎉 Tarea #{i+1} completada con éxito.")

                    except Exception as e_btch:
                        st.error(f"❌ Error en Tarea #{i+1}: {e_btch}. Saltando a la siguiente...")
                    finally:
                        if 'pipeline' in locals() and 'ctx' in locals():
                            pipeline.cleanup(ctx)

                if completed_batch_videos:
                    st.markdown("---")
                    st.subheader("📊 Resumen del Super Batch")
                    for t_name, out_p in completed_batch_videos:
                        st.markdown(f"- ✅ **{t_name}** → `{out_p.name}`")
                    st.video(str(completed_batch_videos[-1][1]))
                    st.balloons()

    # =========================================================================
    # TAB 4: Benchmark de Rendimiento (1080p vs 4K, CPU vs GPU, 3 Blurs)
    # =========================================================================
    with tab_benchmark:
        st.markdown("### ⚡ Benchmark de Rendimiento de Renderizado")
        st.markdown(
            "Evalúa con precisión el rendimiento de tu hardware (**CPU** vs **GPU**) en resoluciones **1080p** y **4K**, "
            "comparando los **3 niveles de intensidad de desenfoque** (100% actual, 75% y 50%). "
            "El video se corta automáticamente de **0 a 10 segundos** para calcular el tiempo exacto por segundo de video, "
            "guardando las muestras de prueba únicamente en `temp/benchmarks/` sin alterar tu carpeta de producción `output/`."
        )

        from benchmark import (
            run_full_benchmark,
            load_benchmark_history,
            clear_benchmark_history
        )

        col_bench_in, col_bench_btn = st.columns([7, 3])
        with col_bench_in:
            bench_source = st.text_input(
                "🔗 Enlace del Trailer de YouTube o Archivo Local",
                placeholder="https://www.youtube.com/watch?v=... o temp/test.mp4",
                key="bench_source_input",
                help="Se cortará automáticamente del segundo 0 al 10 para medir la velocidad exacta de renderizado."
            )
        with col_bench_btn:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            bench_run_clicked = st.button(
                "🚀 Iniciar Benchmark Completo (12 Pruebas)",
                key="btn_run_benchmark",
                use_container_width=True,
                type="primary"
            )

        if bench_run_clicked:
            if not bench_source.strip():
                st.error("❌ Introduce un enlace de YouTube o ruta de video para ejecutar el benchmark.")
            else:
                p_bar = st.progress(0)
                status_txt = st.empty()

                def bench_progress_cb(step, total, msg):
                    pct = int((step / total) * 100) if total > 0 else 0
                    p_bar.progress(pct)
                    status_txt.markdown(f"⏳ **[{step}/{total}]** {msg}")

                try:
                    with st.spinner("Ejecutando suite de benchmark (12 combinaciones)..."):
                        b_res = run_full_benchmark(
                            source_input=bench_source.strip(),
                            ffmpeg_bin=default_config.ffmpeg_path,
                            progress_callback=bench_progress_cb
                        )
                        st.session_state["active_benchmark_result"] = b_res
                        st.success("🎉 ¡Benchmark de 12 pruebas completado con éxito!")
                        st.balloons()
                except Exception as e_bench:
                    st.error(f"Error durante el benchmark: {e_bench}")

        # Display active or latest benchmark result
        active_b = st.session_state.get("active_benchmark_result")
        if not active_b:
            history = load_benchmark_history()
            if history:
                active_b = history[0]

        if active_b:
            st.markdown("---")
            st.markdown(f"#### 📊 Resultados de la Prueba: `{active_b.get('date', '')}` (Clip: 10.0s)")

            # KPI Cards
            kpi_1, kpi_2, kpi_3, kpi_4 = st.columns(4)
            with kpi_1:
                st.metric(
                    label="🏆 Configuración Más Rápida",
                    value=f"{active_b.get('fastest_time_sec', 0)}s",
                    delta=f"{active_b.get('fastest_time_per_sec', 0)} s/s ({active_b.get('fastest_combo', '')})",
                    delta_color="normal"
                )
            with kpi_2:
                st.metric(
                    label="⚡ Aceleración GPU vs CPU",
                    value=f"{active_b.get('gpu_speedup_percent', 0):+.1f}%",
                    delta=f"Promedio GPU: {active_b.get('avg_gpu_sec', 0)}s vs CPU: {active_b.get('avg_cpu_sec', 0)}s"
                )
            with kpi_3:
                st.metric(
                    label="📐 Impacto 4K vs 1080p",
                    value=f"{active_b.get('ratio_4k_vs_1080p', 0)}x tiempo",
                    delta=f"1080p: {active_b.get('avg_1080p_sec', 0)}s | 4K: {active_b.get('avg_4k_sec', 0)}s",
                    delta_color="inverse"
                )
            with kpi_4:
                st.metric(
                    label="🌫️ Reducción Blur (50% vs 100%)",
                    value=f"{active_b.get('blur_reduction_percent', 0):+.1f}%",
                    delta=f"100%: {active_b.get('avg_blur_100_sec', 0)}s | 50%: {active_b.get('avg_blur_50_sec', 0)}s"
                )

            # Interactive Table
            st.markdown("##### 📋 Tabla Comparativa de Rendimiento (12 Combinaciones)")
            import pandas as pd
            table_rows = []
            for item in active_b.get("results", []):
                table_rows.append({
                    "Resolución": item["resolution_label"],
                    "Hardware": item["hardware"],
                    "Codec": item["codec"],
                    "Intensidad Blur": item["blur_level"],
                    "Tiempo Total (s)": item["render_time_sec"],
                    "Tiempo / Seg Video (s/s)": item["time_per_sec_video"],
                    "Velocidad (x Realtime)": f"{item['speedup_realtime']}x",
                    "Tamaño (MB)": item["file_size_mb"]
                })
            df_bench = pd.DataFrame(table_rows)
            df_bench = df_bench.sort_values(by="Tiempo Total (s)", ascending=True)
            st.dataframe(df_bench, use_container_width=True)

            # Visual Quality & Blur Gallery
            st.markdown("##### 👁️ Galería Visual Comparativa (Sin guardar en output)")
            st.caption("Inspecciona visualmente el aspecto del desenfoque y la calidad de video renderizada en temp/benchmarks/:")
            
            gal_col_res, gal_col_hw = st.columns(2)
            with gal_col_res:
                view_res = st.radio("Resolución a Visualizar", options=["1080p", "4K"], horizontal=True, key="bench_view_res")
            with gal_col_hw:
                view_hw = st.radio("Hardware a Visualizar", options=["GPU", "CPU"], horizontal=True, key="bench_view_hw")

            filtered_clips = [
                r for r in active_b.get("results", [])
                if r["resolution"] == view_res and r["hardware"] == view_hw
            ]

            cols_video = st.columns(3)
            for idx, clip_info in enumerate(filtered_clips):
                with cols_video[idx]:
                    st.markdown(f"**Blur {clip_info['blur_label']}** ({clip_info['render_time_sec']}s • {clip_info['speedup_realtime']}x)")
                    v_p = clip_info.get("output_file")
                    if v_p and Path(v_p).exists():
                        st.video(v_p)
                    else:
                        st.info("Archivo no disponible")

        # History expander
        history_list = load_benchmark_history()
        if history_list:
            with st.expander(f"📜 Historial de Benchmarks Guardados ({len(history_list)} ejecuciones)", expanded=False):
                col_h_txt, col_h_del = st.columns([8, 2])
                with col_h_del:
                    if st.button("🗑️ Borrar Historial", key="btn_clear_bench_history", use_container_width=True):
                        clear_benchmark_history()
                        st.session_state["active_benchmark_result"] = None
                        st.toast("🗑️ Historial de benchmarks borrado")
                        st.rerun()

                for h_idx, h_item in enumerate(history_list):
                    st.markdown(
                        f"**#{h_idx+1} [{h_item.get('date')}]** — Más rápido: `{h_item.get('fastest_combo')}` en `{h_item.get('fastest_time_sec')}s` "
                        f"| GPU Speedup: `+{h_item.get('gpu_speedup_percent')}%` | 4K vs 1080p: `{h_item.get('ratio_4k_vs_1080p')}x`"
                    )


if __name__ == "__main__":
    main()
