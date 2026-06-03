import configparser
import os
import matplotlib.pyplot as plt
from core.tools import log_error


class PlotConfig:
    def __init__(self, config_path='config.ini'):
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(config_path, encoding='utf-8')
        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'graphs')

    # ------------------------------------------------------------
    # Публичный метод для обычного графика (одна секция)
    # ------------------------------------------------------------
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

            # Получаем оси для секции
            fig, axes_tuple = self._create_axes_for_section(section, df, return_fig=True)
            if fig is None:
                return None

            # Сохраняем только если upload=True
            if upload:
                os.makedirs(self.output_dir, exist_ok=True)
                full_path = os.path.join(self.output_dir, save)
                fig.savefig(full_path, dpi=150)
                plt.close(fig)
                return full_path
            else:
                # Не сохраняем, но фигуру нужно закрыть
                plt.close(fig)
                return None

        except Exception as e:
            log_error(f"Ошибка при построении графика для секции '{section}': {e}")
            return None
        finally:
            if plt.get_fignums():
                plt.close('all')

    # ------------------------------------------------------------
    # Публичный метод для сабплота
    # ------------------------------------------------------------
    def build_subplot(self, subplot_section, df):
        """
        Строит сабплот из нескольких секций Plot_*, объединённых на одном изображении.
        Возвращает путь к сохранённому файлу или None.
        """
        try:
            if not self.config.has_section(subplot_section):
                log_error(f"Секция '{subplot_section}' отсутствует")
                return None
            if df is None or df.empty:
                log_error(f"DataFrame пуст или None для '{subplot_section}'")
                return None

            sections_str = self.config.get(subplot_section, 'sections')
            plot_sections = [s.strip() for s in sections_str.split(',')]
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

            # Создаём общий figure
            fig, axes = plt.subplots(rows, cols, figsize=figsize, squeeze=False)
            axes_flat = axes.flatten()

            # Для каждой секции строим её содержимое в своей ячейке
            for idx, section in enumerate(plot_sections):
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

            # Общий заголовок
            suptitle = self.config.get(subplot_section, 'title', fallback='')
            if suptitle:
                fig.suptitle(suptitle, fontsize=14)

            # Убираем пустые ячейки, если их больше чем секций
            for j in range(len(plot_sections), total_cells):
                axes_flat[j].set_visible(False)

            fig.tight_layout(rect=[0, 0, 1, 0.95])  # rect для общего заголовка

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

    # ------------------------------------------------------------
    # Внутренние методы для построения осей
    # ------------------------------------------------------------
    def _create_axes_for_section(self, section, df, external_ax=None, return_fig=False):
        """
        Универсальный построитель осей.
        Если external_ax задан – рисуем на нём (и создаём twinx при необходимости).
        Если external_ax=None – создаём собственную фигуру.
        Возвращает:
          - при return_fig=True: (fig, None) если успешно, иначе (None, None)
          - иначе: True/False
        """
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

    def _build_single_axis(self, section, df, external_ax, return_fig):
        """Обычный график с одной осью Y."""
        cols_str = self.config.get(section, 'columns')
        columns = [c.strip() for c in cols_str.split(',')]
        missing = [c for c in columns if c not in df.columns]
        if missing:
            log_error(f"Секция '{section}': отсутствуют колонки {missing}")
            return (None, None) if return_fig else False

        kind = self.config.get(section, 'kind')
        ylabel = self.config.get(section, 'ylabel', fallback='')
        colors = self._get_colors_for_section(section)

        # Создаём фигуру, если нет внешней оси
        if external_ax is None:
            figsize_str = self.config.get(section, 'figsize')
            figsize = tuple(map(int, figsize_str.split(',')))
            fig, ax = plt.subplots(figsize=figsize)
        else:
            fig = external_ax.figure
            ax = external_ax

        if colors:
            df[columns].plot(kind=kind, ax=ax, legend=False, color=colors[:len(columns)])
        else:
            df[columns].plot(kind=kind, ax=ax, legend=False)

        if ylabel:
            ax.set_ylabel(ylabel)
        if len(columns) > 1:
            ax.legend(loc='best')

        if return_fig:
            return fig, ax
        return True

    def _build_multi_axes(self, section, df, left_str, right1_str, right2_str,
                          external_ax, return_fig):
        """График с двумя или тремя осями Y."""
        left_cols = [c.strip() for c in left_str.split(',')]
        right1_cols = [c.strip() for c in right1_str.split(',')] if right1_str else []
        right2_cols = [c.strip() for c in right2_str.split(',')] if right2_str else []

        # Проверка колонок
        all_cols = left_cols + right1_cols + right2_cols
        missing = [c for c in all_cols if c not in df.columns]
        if missing:
            log_error(f"Секция '{section}': отсутствуют колонки {missing}")
            return (None, None) if return_fig else False

        kind = self.config.get(section, 'kind')
        colors = self._get_colors_for_section(section)

        # Создаём оси
        if external_ax is None:
            figsize_str = self.config.get(section, 'figsize')
            figsize = tuple(map(int, figsize_str.split(',')))
            fig, ax_left = plt.subplots(figsize=figsize)
        else:
            fig = external_ax.figure
            ax_left = external_ax

        # Создаём правые оси
        ax_right1 = ax_left.twinx() if right1_cols else None
        ax_right2 = None
        if right2_cols:
            ax_right2 = ax_left.twinx()
            offset = 0.08
            ax_right2.spines['right'].set_position(('axes', 1 + offset))

        # Рисуем левую ось
        color_idx = 0
        if left_cols:
            c = colors[color_idx:color_idx+len(left_cols)] if colors else None
            df[left_cols].plot(kind=kind, ax=ax_left, legend=False, color=c)
            color_idx += len(left_cols)
        if left_cols:
            left_ylabel = self.config.get(section, 'left_ylabel', fallback='')
            if left_ylabel:
                ax_left.set_ylabel(left_ylabel)

        # Рисуем первую правую ось
        if right1_cols and ax_right1:
            c = colors[color_idx:color_idx+len(right1_cols)] if colors else None
            df[right1_cols].plot(kind=kind, ax=ax_right1, legend=False, color=c)
            color_idx += len(right1_cols)
            right1_ylabel = self.config.get(section, 'right1_ylabel', fallback='')
            if not right1_ylabel:
                right1_ylabel = self.config.get(section, 'right_ylabel', fallback='')
            if right1_ylabel:
                ax_right1.set_ylabel(right1_ylabel)

        # Рисуем вторую правую ось
        if right2_cols and ax_right2:
            c = colors[color_idx:color_idx+len(right2_cols)] if colors else None
            df[right2_cols].plot(kind=kind, ax=ax_right2, legend=False, color=c)
            right2_ylabel = self.config.get(section, 'right2_ylabel', fallback='')
            if right2_ylabel:
                ax_right2.set_ylabel(right2_ylabel)

        # Сборка легенды
        lines, labels = [], []
        for ax in [ax_left, ax_right1, ax_right2]:
            if ax:
                l, lab = ax.get_legend_handles_labels()
                lines.extend(l)
                labels.extend(lab)
        if lines:
            ax_left.legend(lines, labels, loc='best')

        if return_fig:
            return fig, ax_left
        return True

    def _get_colors_for_section(self, section):
        colors_str = self.config.get(section, 'colors', fallback=None)
        if colors_str:
            return [c.strip() for c in colors_str.split(',')]
        return None