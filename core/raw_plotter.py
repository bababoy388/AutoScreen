"""
Обёртка над plot_csv_columns.py.
Единый конфиг графиков — plot_config.json, общий для всех заводов.
"""
import os
import tempfile
from pathlib import Path

from core.plot_csv_columns import load_config, load_plot_data, make_plot
from core.tools import log_error


PLOT_CONFIG_PATH = str(Path(__file__).resolve().parent / 'plot_config.json')


class RawPlotConfig:
    def __init__(self, plot_config_path=None):
        # если путь не передан — берём дефолт рядом с файлом
        self.plot_config_path = str(plot_config_path) if plot_config_path else PLOT_CONFIG_PATH

    # ------------------------------------------------------------------
    # Что рисовать (для графика)
    # ------------------------------------------------------------------

    def get_plot_fields(self):
        """
        Колонки, которые нужны для ГРАФИКА (из plot_config.json).
        Используется только для отладки/логов — build_raw_plot сам
        разберётся, какие колонки брать.
        """
        cfg = load_config(Path(self.plot_config_path))
        x_col = str(cfg['x_column'])

        fields = []
        if x_col.lower() not in ('row_number', 'row', '__row_number__'):
            fields.append(x_col)

        for s in cfg['series']:
            col = s['column']
            if col not in fields:
                fields.append(col)

        return fields

    # ------------------------------------------------------------------
    # Что скачивать (для CSV)
    # ------------------------------------------------------------------

    def get_download_fields(self):
        """
        Колонки для параметра fields в /api/RawData/csv.

        None  -> параметр fields не отправляется, сервер отдаёт ВСЕ колонки.
        [...] -> отправляется явный список (если сервер требует fields).

        Сейчас возвращаем None, чтобы CSV приходил полный.
        Если сервер не отдаёт данные без fields — раскомментируй список ниже.
        """
        return None

        # Если серверу нужен явный список:
        # return [
        #     "time", "FrameCnt", "rotationDuration",
        #     "ExAccVibX", "ExAccVibY", "Tensor",
        #     "v0_1", "v0_2", "v0_3",
        #     "v1_1", "v1_2", "v1_3",
        #     "v2_1", "v2_2", "v2_3", "v2_4", "v2_5", "v2_6", "v2_7", "v2_8",
        # ]

    # ------------------------------------------------------------------
    # Построение
    # ------------------------------------------------------------------

    def build_raw_plot(self, df, section=None, save_path=None):
        """
        Строит график по df, используя plot_config.json.
        df — DataFrame с колонками, совпадающими с series[].column.
        Лишние колонки в df игнорируются.

        section — только для имени PNG, если save_path не задан.
        Возвращает путь к сохранённому PNG.
        """
        # каждый вызов — свежий конфиг, чтобы не мутировать общий словарь
        cfg = load_config(Path(self.plot_config_path))

        if save_path is None:
            base = section or 'raw'
            save_path = f"{base}_raw.png"

        cfg['output_path'] = str(save_path)
        cfg['save_plot'] = True
        cfg['show_plot'] = False

        # plot_csv_columns работает с CSV-файлом — сохраняем df во временный
        fd, tmp_csv = tempfile.mkstemp(suffix='.csv', prefix='raw_plot_')
        os.close(fd)
        try:
            df.to_csv(tmp_csv, index=False)

            x_values, series_data = load_plot_data(cfg, Path(tmp_csv))
            out_path, _ = make_plot(cfg, x_values, series_data)
            return str(out_path)
        finally:
            try:
                os.remove(tmp_csv)
            except OSError:
                pass