import configparser
import os
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from itertools import cycle
from core.tools import log_error


class PlotConfig:
    def __init__(self, config_path='config.ini'):
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(config_path, encoding='utf-8')
        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'graphs')
        # Максимальный допустимый разрыв между точками (в минутах)
        self.MAX_GAP_MINUTES = 5

    def build_for_section(self, section, df):
        try:
            if not self.config.has_section(section):
                log_error(f"Секция '{section}' отсутствует в конфигурационном файле")
                return None
            if df is None or df.empty:
                log_error(f"DataFrame пуст или None для секции '{section}'")
                return None

            upload = self.config.getboolean(section, 'upload', fallback=True)
            save = self.config.get(section, 'save')

            fig, axes_tuple = self._create_axes_for_section(section, df, return_fig=True)
            if fig is None:
                return None

            if upload:
                os.makedirs(self.output_dir, exist_ok=True)
                full_path = os.path.join(self.output_dir, save)
                fig.savefig(full_path, dpi=150)
                plt.close(fig)
                return full_path
            else:
                plt.close(fig)
                return None

        except Exception as e:
            log_error(f"Ошибка при построении графика для секции '{section}': {e}")
            return None
        finally:
            if plt.get_fignums():
                plt.close('all')

    def build_subplot(self, subplot_section, df):
        try:
            if not self.config.has_section(subplot_section):
                log_error(f"Секция '{subplot_section}' отсутствует")
                return None
            if df is None or df.empty:
                log_error(f"DataFrame пуст или None для '{subplot_section}'")
                return None

            plots_str = self.config.get(subplot_section, 'plots', fallback=None)
            if plots_str is None:
                plots_str = self.config.get(subplot_section, 'sections', fallback=None)
            if plots_str is None:
                log_error(f"В секции '{subplot_section}' не указаны plots или sections")
                return None

            plot_sections = [s.strip() for s in plots_str.split(',') if s.strip()]
            if not plot_sections:
                log_error(f"В секции '{subplot_section}' нет графиков")
                return None

            rows = self.config.getint(subplot_section, 'rows', fallback=1)
            cols = self.config.getint(subplot_section, 'cols', fallback=1)
            total_cells = rows * cols
            if len(plot_sections) > total_cells:
                log_error(f"Слишком много секций ({len(plot_sections)}) для сетки {rows}x{cols}")
                return None

            figsize_str = self.config.get(subplot_section, 'figsize', fallback='12,8')
            try:
                figsize = tuple(map(int, figsize_str.split(',')))
            except:
                figsize = (12, 8)

            grid = self.config.getboolean(subplot_section, 'grid', fallback=True)
            save = self.config.get(subplot_section, 'save')

            fig, axes = plt.subplots(rows, cols, figsize=figsize, squeeze=False)
            axes_flat = axes.flatten()

            for idx, section in enumerate(plot_sections):
                if idx >= total_cells:
                    break
                if not self.config.has_section(section):
                    log_error(f"Секция '{section}' не найдена, пропуск в сабплоте")
                    continue
                ax_main = axes_flat[idx]
                # Строим на переданной оси (с возможными дополнительными осями twinx)
                success = self._create_axes_for_section(section, df, external_ax=ax_main)
                if not success:
                    log_error(f"Не удалось построить секцию '{section}' в сабплоте")
                    continue
                ax_main.set_title(self.config.get(section, 'title', fallback=''))
                ax_main.grid(grid)

            suptitle = self.config.get(subplot_section, 'title', fallback='')
            if suptitle:
                fig.suptitle(suptitle, fontsize=14)

            for j in range(len(plot_sections), total_cells):
                axes_flat[j].set_visible(False)

            fig.tight_layout(rect=[0, 0, 1, 0.95])

            os.makedirs(self.output_dir, exist_ok=True)
            full_path = os.path.join(self.output_dir, save)
            fig.savefig(full_path, dpi=150)
            plt.close(fig)
            return full_path

        except Exception as e:
            log_error(f"Ошибка при построении сабплота '{subplot_section}': {e}")
            return None
        finally:
            if plt.get_fignums():
                plt.close('all')

    def _create_axes_for_section(self, section, df, external_ax=None, return_fig=False):
        # Определяем тип осей
        left_str = self.config.get(section, 'left_columns', fallback=None)
        right1_str = self.config.get(section, 'right1_columns', fallback=None)
        right2_str = self.config.get(section, 'right2_columns', fallback=None)
        if not right1_str:
            right1_str = self.config.get(section, 'right_columns', fallback=None)

        # Получаем данные колонок
        if left_str and (right1_str or right2_str):
            # Многоосевой режим
            return self._build_multi_axes(section, df, left_str, right1_str, right2_str,
                                          external_ax, return_fig)
        else:
            # Одноосевой режим
            return self._build_single_axis(section, df, external_ax, return_fig)

    # ------------------ РАЗБИЕНИЕ НА ЧАНКИ ------------------
    def _get_continuous_chunks(self, df, gap_minutes=None):
        """
        Разбивает DataFrame на части, где разница между временными метками индекса
        не превышает заданного порога (gap_minutes). Возвращает список под-DataFrame.
        Если индекс не является datetime, возвращает [df] (без разбиения).
        """
        if gap_minutes is None:
            gap_minutes = self.MAX_GAP_MINUTES
        if not isinstance(df.index, pd.DatetimeIndex):
            # Если индекс не временной, разбиение невозможно — возвращаем исходный DF
            return [df]
        # Вычисляем разницу между соседними индексами
        diffs = df.index.to_series().diff()
        # Находим индексы, где разница превышает порог
        split_points = diffs[diffs > pd.Timedelta(minutes=gap_minutes)].index
        if len(split_points) == 0:
            return [df]
        # Разбиваем по найденным точкам
        chunks = []
        start = 0
        for split_idx in split_points:
            # находим позицию (номер строки) точки разрыва
            pos = df.index.get_loc(split_idx)
            if pos > start:
                chunks.append(df.iloc[start:pos])
            start = pos
        # последний кусок
        if start < len(df):
            chunks.append(df.iloc[start:])
        # отбрасываем пустые чанки
        return [chunk for chunk in chunks if not chunk.empty]

    # ------------------ ОТРИСОВКА С РАЗРЫВАМИ (без легенды) ------------------
    def _plot_columns_with_gaps(self, ax, df, columns, kind, colors=None):
        """
        Рисует график для указанных столбцов с учётом временных разрывов.
        Легенда всегда отключена (legend=False), чтобы мы могли создать её вручную позже.
        """
        if kind == 'line' and isinstance(df.index, pd.DatetimeIndex):
            chunks = self._get_continuous_chunks(df)
            for chunk in chunks:
                if not chunk.empty:
                    chunk[columns].plot(kind=kind, ax=ax, legend=False, color=colors)
        else:
            # Для bar и других типов разбиение не применяем
            df[columns].plot(kind=kind, ax=ax, legend=False, color=colors)

    # ------------------ ОДНООСЕВОЙ РЕЖИМ (с ручной легендой) ------------------
    def _build_single_axis(self, section, df, external_ax, return_fig):
        cols_str = self.config.get(section, 'columns')
        columns = [c.strip() for c in cols_str.split(',')]
        missing = [c for c in columns if c not in df.columns]
        if missing:
            log_error(f"Секция '{section}': отсутствуют колонки {missing}")
            return (None, None) if return_fig else False

        kind = self.config.get(section, 'kind')
        ylabel = self.config.get(section, 'ylabel', fallback='')
        colors = self._get_colors_for_section(section)

        if external_ax is None:
            figsize_str = self.config.get(section, 'figsize')
            figsize = tuple(map(int, figsize_str.split(',')))
            fig, ax = plt.subplots(figsize=figsize)
        else:
            fig = external_ax.figure
            ax = external_ax

        # Рисуем все чанки без легенды
        self._plot_columns_with_gaps(ax, df, columns, kind, colors)

        if ylabel:
            ax.set_ylabel(ylabel)

        # --- СОЗДАЁМ ЛЕГЕНДУ ВРУЧНУЮ ---
        if len(columns) > 0:
            # Если цвета не заданы, генерируем стандартные
            if colors is None:
                default_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
                color_cycle = cycle(default_colors)
                colors = [next(color_cycle) for _ in columns]
            else:
                # Если заданы, но не хватает, дополняем стандартными
                if len(colors) < len(columns):
                    default_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
                    extra = cycle(default_colors)
                    while len(colors) < len(columns):
                        colors.append(next(extra))
                elif len(colors) > len(columns):
                    colors = colors[:len(columns)]

            handles = [Line2D([0], [0], color=color, lw=2) for color in colors]
            labels = columns
            ax.legend(handles, labels, loc='best')

        if return_fig:
            return fig, ax
        return True

    # ------------------ МНОГООСЕВОЙ РЕЖИМ (с ручной легендой) ------------------
    def _build_multi_axes(self, section, df, left_str, right1_str, right2_str,
                          external_ax, return_fig):
        left_cols = [c.strip() for c in left_str.split(',')]
        right1_cols = [c.strip() for c in right1_str.split(',')] if right1_str else []
        right2_cols = [c.strip() for c in right2_str.split(',')] if right2_str else []

        all_cols = left_cols + right1_cols + right2_cols
        missing = [c for c in all_cols if c not in df.columns]
        if missing:
            log_error(f"Секция '{section}': отсутствуют колонки {missing}")
            return (None, None) if return_fig else False

        kind = self.config.get(section, 'kind')
        colors = self._get_colors_for_section(section)

        if external_ax is None:
            figsize_str = self.config.get(section, 'figsize')
            figsize = tuple(map(int, figsize_str.split(',')))
            fig, ax_left = plt.subplots(figsize=figsize)
        else:
            fig = external_ax.figure
            ax_left = external_ax

        ax_right1 = ax_left.twinx() if right1_cols else None
        ax_right2 = None
        if right2_cols:
            ax_right2 = ax_left.twinx()
            ax_right2.spines['right'].set_position(('axes', 1 + 0.08))
            # Цвет для правой оси (если есть)
            if colors and len(colors) > len(left_cols) + len(right1_cols):
                color2 = colors[len(left_cols) + len(right1_cols)]
                ax_right2.tick_params(axis='y', labelcolor=color2)
                ax_right2.yaxis.label.set_color(color2)

        # Храним пары (ось, список колонок) для последующего построения и сбора цветов
        axes_columns = [(ax_left, left_cols)]
        if ax_right1:
            axes_columns.append((ax_right1, right1_cols))
        if ax_right2:
            axes_columns.append((ax_right2, right2_cols))

        # Если цвета не заданы, генерируем стандартные для всех колонок
        if colors is None:
            default_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
            color_cycle = cycle(default_colors)
            colors = [next(color_cycle) for _ in all_cols]
        else:
            # Дополняем или обрезаем до нужного количества
            if len(colors) < len(all_cols):
                default_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
                extra = cycle(default_colors)
                while len(colors) < len(all_cols):
                    colors.append(next(extra))
            elif len(colors) > len(all_cols):
                colors = colors[:len(all_cols)]

        # Строим графики для каждой оси, передавая соответствующие цвета
        color_idx = 0
        for ax, cols in axes_columns:
            if cols:
                col_colors = colors[color_idx:color_idx + len(cols)]
                self._plot_columns_with_gaps(ax, df, cols, kind, col_colors)
                color_idx += len(cols)

        # Устанавливаем подписи осей
        left_ylabel = self.config.get(section, 'left_ylabel', fallback='')
        if left_ylabel:
            ax_left.set_ylabel(left_ylabel)

        if ax_right1:
            right1_ylabel = self.config.get(section, 'right1_ylabel', fallback='')
            if not right1_ylabel:
                right1_ylabel = self.config.get(section, 'right_ylabel', fallback='')
            if right1_ylabel:
                ax_right1.set_ylabel(right1_ylabel)

        if ax_right2:
            right2_ylabel = self.config.get(section, 'right2_ylabel', fallback='')
            if right2_ylabel:
                ax_right2.set_ylabel(right2_ylabel)
                ax_right2.yaxis.label.set_color('black')
                ax_right2.tick_params(axis='y', labelcolor='black')

        # --- СОЗДАЁМ ОБЩУЮ ЛЕГЕНДУ ВРУЧНУЮ (на главной оси) ---
        handles = [Line2D([0], [0], color=color, lw=2) for color in colors]
        labels = all_cols
        ax_left.legend(handles, labels, loc='best')

        fig.tight_layout()

        if return_fig:
            return fig, ax_left
        return True

    def _get_colors_for_section(self, section):
        colors_str = self.config.get(section, 'colors', fallback=None)
        if colors_str:
            return [c.strip() for c in colors_str.split(',')]
        return None