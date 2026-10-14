import configparser

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from core.tools import log_error


RAW_PLOTS_SECTION = 'RawPlots'


class RawPlotConfig:
    def __init__(self, config_path='config.ini'):
        self.config_path = config_path

    # ------------------------------------------------------------------
    # Чтение конфига
    # ------------------------------------------------------------------

    def _read_cfg(self):
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(self.config_path, encoding='utf-8')
        if not cfg.has_section(RAW_PLOTS_SECTION):
            raise ValueError(f"В конфиге нет секции [{RAW_PLOTS_SECTION}]")
        return cfg

    @staticmethod
    def _parse_series(series_str):
        """'ax0_3:0.05035,ax1_2' -> [('ax0_3', 0.05035), ('ax1_2', 1.0)]"""
        result = []
        for item in series_str.split(','):
            item = item.strip()
            if not item:
                continue
            if ':' in item:
                field, mult_str = item.split(':', 1)
                try:
                    mult = float(mult_str.strip())
                except ValueError:
                    log_error(
                        f"Некорректный множитель '{mult_str}' для поля '{field}', беру 1.0"
                    )
                    mult = 1.0
            else:
                field, mult = item, 1.0
            result.append((field.strip(), mult))
        return result

    def get_raw_fields(self):
        """Список полей для запроса к серверу (общий для всех заводов)."""
        cfg = self._read_cfg()
        raw = cfg.get(RAW_PLOTS_SECTION, 'raw_fields', fallback='')
        return [f.strip() for f in raw.split(',') if f.strip()]

    def _read_subplots(self):
        cfg = self._read_cfg()
        params = dict(cfg.items(RAW_PLOTS_SECTION))

        subplots = []
        i = 1
        while f'raw_plot_{i}' in params:
            raw = params[f'raw_plot_{i}']
            if '|' not in raw:
                log_error(
                    f"raw_plot_{i} в [{RAW_PLOTS_SECTION}]: нет разделителя '|', пропускаю"
                )
                i += 1
                continue
            title_part, series_part = raw.split('|', 1)
            subplots.append({
                'title': title_part.strip(),
                'series': self._parse_series(series_part.strip()),
            })
            i += 1
        return subplots

    # ------------------------------------------------------------------
    # Построение
    # ------------------------------------------------------------------

    def build_raw_plot(self, df, section=None, save_path=None):
        """
        Строит многосубплотный график по сырым данным.
        df — DataFrame с колонками из raw_fields (+ 'time', если есть).
        section — только для имени файла, если save_path не задан.
        """
        subplots = self._read_subplots()
        if not subplots:
            raise ValueError(
                f"В секции [{RAW_PLOTS_SECTION}] нет ни одного raw_plot_N"
            )

        # --- ось X ---
        if 'time' in df.columns:
            x = pd.to_datetime(df['time'], errors='coerce', utc=True)
        else:
            x = range(len(df))

        n = len(subplots)
        fig, axes = plt.subplots(n, 1, figsize=(12, 2.0 * n), sharex=True)
        if n == 1:
            axes = [axes]

        color_cycle = plt.rcParams['axes.prop_cycle'].by_key()['color']
        color_idx = 0

        for ax, sp in zip(axes, subplots):
            ax.set_title(sp['title'], fontsize=12)
            ax.grid(True, alpha=0.3)

            for field, mult in sp['series']:
                if field not in df.columns:
                    log_error(f"Поле '{field}' отсутствует в df, пропускаю")
                    continue
                y = pd.to_numeric(df[field], errors='coerce') * mult
                color = color_cycle[color_idx % len(color_cycle)]
                color_idx += 1
                ax.plot(x, y, label=f"{field} x {mult}", color=color, linewidth=0.9)
                ax.legend(loc='upper right', fontsize=9)

        plt.tight_layout()

        if save_path is None:
            base = section or 'raw'
            save_path = f"{base}_raw.png"

        plt.savefig(save_path, dpi=100)
        plt.close(fig)
        return save_path