"""
Обёртка над plot_csv_columns.py.
Читает настройки графиков прямо из config.ini для конкретного завода (секции).
"""
import configparser
import json
import os
import tempfile
from pathlib import Path

from core.plot_csv_columns import load_config, load_plot_data, make_plot
from core.tools import log_error


class RawPlotConfig:
    def __init__(self, config_path='config.ini'):
        self.config_path = config_path

    # ------------------------------------------------------------------
    # Что скачивать (для CSV)
    # ------------------------------------------------------------------

    def get_download_fields(self, section=None):
        """
        Возвращает список полей для скачивания, если в секции задан raw_fields.
        Если не задан — возвращает None (сервер отдаст все колонки).
        """
        if not section:
            return None

        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.config_path, encoding='utf-8')

        if cfg.has_option(section, 'raw_fields'):
            raw = cfg.get(section, 'raw_fields')
            fields = [f.strip() for f in raw.split(',') if f.strip()]
            return fields if fields else None

        return None

    # ------------------------------------------------------------------
    # Построение
    # ------------------------------------------------------------------

    def build_raw_plot(self, df, section, save_path=None):
        """
        Строит график по df, используя raw_plot_N из секции завода в config.ini.
        """
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.config_path, encoding='utf-8')

        if not cfg.has_section(section):
            raise ValueError(f"Секция {section} не найдена в конфиге")

        subplots = []
        series = []
        i = 1

        # Читаем raw_plot_1, raw_plot_2 и т.д. из секции завода
        while True:
            key = f'raw_plot_{i}'
            if not cfg.has_option(section, key):
                break

            val = cfg.get(section, key)
            if '|' not in val:
                log_error(f"{section}: {key} без разделителя '|', пропускаю")
                i += 1
                continue

            title, series_str = val.split('|', 1)
            subplot_id = f"subplot_{i}"
            subplots.append({
                "id": subplot_id,
                "title": title.strip(),
                "y_axis_mode": "separate"
            })

            for item in series_str.split(','):
                item = item.strip()
                if not item:
                    continue

                if ':' in item:
                    col, ratio_str = item.split(':', 1)
                    try:
                        ratio = float(ratio_str)
                    except ValueError:
                        ratio = 1.0
                else:
                    col, ratio = item, 1.0

                series.append({
                    "column": col.strip(),
                    "label": col.strip(),
                    "ratio": ratio,
                    "subplot": subplot_id
                })
            i += 1

        if not subplots:
            raise ValueError(f"В секции {section} нет ни одного raw_plot_N")

        if save_path is None:
            save_path = f"{section}_raw.png"

        # Формируем словарь для plot_csv_columns.py
        plot_config = {
            "x_column": "row_number",
            "series": series,
            "subplots": subplots,
            "save_plot": True,
            "show_plot": False,
            "output_path": str(save_path),
            "title": f"RAW {section}",
            "figsize": [12, 2.0 * len(subplots)],
            "dpi": 150,
            "share_x": True,
            "grid": True
        }

        # Сохраняем во временный JSON
        fd_json, tmp_json = tempfile.mkstemp(suffix='.json', prefix='plot_cfg_')
        with os.fdopen(fd_json, 'w', encoding='utf-8') as f:
            json.dump(plot_config, f)

        # Сохраняем df во временный CSV
        fd_csv, tmp_csv = tempfile.mkstemp(suffix='.csv', prefix='raw_plot_')
        os.close(fd_csv)

        try:
            df.to_csv(tmp_csv, index=False)
            loaded_cfg = load_config(Path(tmp_json))
            x_values, series_data = load_plot_data(loaded_cfg, Path(tmp_csv))
            out_path, _ = make_plot(loaded_cfg, x_values, series_data)
            return str(out_path)
        finally:
            try:
                os.remove(tmp_json)
            except OSError:
                pass
            try:
                os.remove(tmp_csv)
            except OSError:
                pass