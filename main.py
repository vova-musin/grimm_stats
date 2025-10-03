import json
import os
import sys
import shutil
import re
import threading
import hashlib
import tempfile
import webbrowser
from urllib import request as _urlrequest, parse as _urlparse
from http import cookiejar as _cookiejar
import email.utils as _email_utils
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple

from PySide6.QtCore import QTimer, Qt, QRegularExpression
from PySide6.QtGui import QIntValidator, QRegularExpressionValidator, QIcon
from PySide6.QtWidgets import (
	QApplication,
	QMainWindow,
	QTabWidget,
	QWidget,
	QVBoxLayout,
	QHBoxLayout,
	QPushButton,
	QLabel,
	QLineEdit,
	QGroupBox,
	QFormLayout,
	QMessageBox,
	QMenu,
	QCheckBox,
	QSlider,
	QScrollArea,
)
from PySide6.QtWidgets import QFileDialog, QListWidget, QListWidgetItem, QInputDialog, QDialog, QDialogButtonBox
from PySide6.QtWidgets import QCheckBox, QSlider

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


# Источник манифеста по умолчанию (GitHub Raw)
DEFAULT_MANIFEST_URL = "https://raw.githubusercontent.com/vova-musin/grimm_stats/main/version.json"

# Попытка подключить менеджеры крафта из соседней папки проекта
try:
	from craft_manager import CraftManager
	from craft_price_manager import CraftPriceManager
except Exception:
	try:
		proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		discord_dir = os.path.join(proj_root, "discord_mj")
		if os.path.isdir(discord_dir) and discord_dir not in sys.path:
			sys.path.append(discord_dir)
		# Поддержка окружения без python-dotenv (используется в discord_mj/config.py)
		try:
			import dotenv  # type: ignore
		except Exception:
			import types as _types
			dotenv_stub = _types.ModuleType('dotenv')
			setattr(dotenv_stub, 'load_dotenv', lambda *a, **k: None)
			sys.modules['dotenv'] = dotenv_stub
		from craft_manager import CraftManager
		from craft_price_manager import CraftPriceManager
	except Exception:
		CraftManager = None  # type: ignore
		CraftPriceManager = None  # type: ignore

# Локальные fallback-реализации, если внешние модули недоступны
if CraftManager is None or CraftPriceManager is None:
	from typing import Tuple as _Tuple
	import csv as _csv

	class _LocalCraftManager:
		def __init__(self, recipes_file: str) -> None:
			self.recipes_file = recipes_file
			self.recipes: Dict[int, Dict[str, Dict[str, Any]]] = {}
			self.current_level = 1
			self.load_recipes()

		def load_recipes(self) -> None:
			self.recipes = {}
			if not os.path.exists(self.recipes_file):
				# создадим пустой файл с заголовками
				with open(self.recipes_file, 'w', encoding='utf-8', newline='') as f:
					w = _csv.writer(f)
					w.writerow(['# level,name,mat1,qty1,...,mat6,qty6,chance,quantity,fee,desc'])
					w.writerow([])
				return
			try:
				with open(self.recipes_file, 'r', encoding='utf-8') as f:
					r = _csv.reader(f)
					for row in r:
						if not row or (row[0] and row[0].startswith('#')):
							continue
						try:
							level = int(row[0].strip()); name = row[1].strip()
							materials: Dict[str,int] = {}
							for i in range(2, max(2, len(row)-4), 2):
								if i+1 < len(row)-4:
									m = row[i].strip(); q = row[i+1].strip()
									if m and q: materials[m] = int(float(q))
							success = int(float(row[-4])) if len(row) >= 4 else 35
							quantity = int(float(row[-3])) if len(row) >= 3 else 1
							fee = int(float(row[-2])) if len(row) >= 2 else 0
							desc = row[-1] if len(row) >= 1 else ''
							self.recipes.setdefault(level, {})[name] = {
								"materials": materials,
								"success_chance": success,
								"quantity": quantity,
								"craft_fee": fee,
								"description": desc,
							}
						except Exception:
							continue
			except Exception:
				self.recipes = {}

		def save_recipes(self) -> None:
			with open(self.recipes_file, 'w', encoding='utf-8', newline='') as f:
				w = _csv.writer(f)
				w.writerow(['# level,name,mat1,qty1,...,mat6,qty6,chance,quantity,fee,desc'])
				w.writerow([])
				for lvl in sorted(self.recipes.keys()):
					for name, rec in sorted(self.recipes[lvl].items()):
						mats = list((rec.get("materials") or {}).items())[:6]
						flat: List[str] = []
						for m,q in mats: flat += [m, str(int(q))]
						while len(flat) < 12: flat += ["",""]
						row = [str(lvl), name] + flat + [
							str(int(rec.get("success_chance",35))),
							str(int(rec.get("quantity",1))),
							str(int(rec.get("craft_fee",0))),
							rec.get("description",""),
						]
						w.writerow(row)

		def set_current_level(self, level: int) -> None:
			self.current_level = int(level)

		def get_all_recipes(self) -> Dict[int, Dict[str, Dict[str, Any]]]:
			return self.recipes

		def search_recipes(self, q: str) -> List[_Tuple[str, Dict[str, Any], int]]:
			q = (q or '').lower()
			out: List[_Tuple[str, Dict[str, Any], int]] = []
			for lvl, recs in self.recipes.items():
				for name, rec in recs.items():
					if q in name.lower(): out.append((name, rec, lvl))
			return out

		def get_recipe(self, item_name: str) -> Optional[Dict[str, Any]]:
			for lvl, recs in self.recipes.items():
				if item_name in recs: return recs[item_name]
			return None

		def upsert_recipe(self, level: int, name: str, materials: Dict[str,int], success_chance: int, quantity: int, craft_fee: int, description: str) -> None:
			self.recipes.setdefault(int(level), {})[name] = {
				"materials": {k:int(v) for k,v in materials.items()},
				"success_chance": int(success_chance),
				"quantity": int(quantity),
				"craft_fee": int(craft_fee),
				"description": description or '',
			}
			self.save_recipes()

		def _calc_material_cost(self, name: str, qty: int, price_manager: ' _LocalCraftPriceManager') -> Optional[int]:
			# Если материал — результат другого рецепта, используем его себестоимость материалов (без вероятности)
			rec = self.get_recipe(name)
			if rec is not None:
				cost = self.calculate_craft_cost(name, price_manager)
				return None if cost is None else int(cost) * int(qty)
			price = price_manager.get_price(name)
			return None if price is None else int(price) * int(qty)

		def calculate_craft_cost(self, item_name: str, price_manager) -> Optional[int]:
			rec = self.get_recipe(item_name)
			if not rec: return None
			total = 0
			for m,q in (rec.get("materials") or {}).items():
				part = self._calc_material_cost(m, int(q), price_manager)
				if part is None: return None
				total += int(part)
			return total

		def evaluate_profitability(self, item_name: str, price_manager, sell_price: Optional[float]=None) -> Optional[Dict[str, Any]]:
			rec = self.get_recipe(item_name)
			if not rec: return None
			mats = self.calculate_craft_cost(item_name, price_manager)
			if mats is None: return None
			chance = float(max(1, min(100, int(rec.get("success_chance", 35)))))
			fee = float(rec.get("craft_fee", 0))
			quantity = int(rec.get("quantity", 1))
			if sell_price is None:
				sp = price_manager.get_price(item_name)
				sell_price = float(sp) if sp is not None else 0.0
			expected = (float(mats) + fee) / (chance/100.0)
			sell_net = float(sell_price) * quantity * (1.0 - 0.10)
			profit = sell_net - expected
			return {
				"item": item_name,
				"chance": chance,
				"quantity": quantity,
				"materials_cost": float(mats),
				"fee_per_attempt": fee,
				"expected_cost": float(expected),
				"sell_price": float(sell_price),
				"profit": float(profit),
			}

	class _LocalCraftPriceManager:
		def __init__(self, prices_file: str) -> None:
			self.prices_file = prices_file
			self.prices: Dict[str,int] = {}
			self.load_prices()

		def load_prices(self) -> None:
			self.prices = {}
			if not os.path.exists(self.prices_file):
				with open(self.prices_file, 'w', encoding='utf-8', newline='') as f:
					w = _csv.writer(f); w.writerow(['# name,price']); w.writerow([])
				return
			with open(self.prices_file, 'r', encoding='utf-8') as f:
				r = _csv.reader(f)
				for row in r:
					if not row or (row[0] and row[0].startswith('#')): continue
					name = row[0].strip()
					try: price = int(float(row[1].strip()))
					except Exception: continue
					self.prices[name] = price

		def save_prices(self) -> None:
			with open(self.prices_file, 'w', encoding='utf-8', newline='') as f:
				w = _csv.writer(f); w.writerow(['# name,price']); w.writerow([])
				for name, price in sorted(self.prices.items()): w.writerow([name, price])

		def get_price(self, name: str) -> Optional[int]:
			return self.prices.get(name)

# Привязываем fallback классы к именам, если оригиналы недоступны
if CraftManager is None:
	CraftManager = _LocalCraftManager  # type: ignore
if CraftPriceManager is None:
	CraftPriceManager = _LocalCraftPriceManager  # type: ignore

# ------------------------
# Данные и хранилище
# ------------------------
@dataclass
class WorkSession:
	start_iso: str
	end_iso: Optional[str] = None
	category: str = "trucker"  # trucker | farm | mine | fish | mushroom | logger

	def duration_seconds(self) -> int:
		start_dt = datetime.fromisoformat(self.start_iso)
		end_dt = datetime.fromisoformat(self.end_iso) if self.end_iso else datetime.now()
		return int((end_dt - start_dt).total_seconds())


@dataclass
class Transaction:
	amount: int
	type: str  # income | expense
	note: str
	time_iso: str
	category: str = "trucker"


class DayStorage:
	"""Управляет сохранением/загрузкой статистики за день в JSON."""

	def __init__(self, base_dir: str) -> None:
		self.base_dir = base_dir
		self.data_dir = os.path.join(self.base_dir, "data")
		os.makedirs(self.data_dir, exist_ok=True)
		# Миграция данных из старой папки рядом с exe/скриптом
		try:
			if not os.listdir(self.data_dir):
				legacy_root = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
				legacy_dir = os.path.join(legacy_root, "data")
				if os.path.isdir(legacy_dir):
					for name in os.listdir(legacy_dir):
						if name.endswith(".json"):
							shutil.copy2(os.path.join(legacy_dir, name), os.path.join(self.data_dir, name))
		except Exception:
			pass
		# Авто-очистка старых файлов (>30 дней)
		try:
			for name in os.listdir(self.data_dir):
				if name.endswith('.json'):
					path = os.path.join(self.data_dir, name)
					mtime = os.path.getmtime(path)
					age_days = (datetime.now() - datetime.fromtimestamp(mtime)).days
					if age_days > 30:
						os.remove(path)
		except Exception:
			pass

	def _file_for(self, day: date) -> str:
		name = day.strftime("%Y-%m-%d") + ".json"
		return os.path.join(self.data_dir, name)

	def load_day(self, day: date) -> Dict[str, Any]:
		file_path = self._file_for(day)
		if not os.path.exists(file_path):
			return {"sessions": [], "transactions": []}
		with open(file_path, "r", encoding="utf-8") as f:
			return json.load(f)

	def save_day(self, day: date, data: Dict[str, Any]) -> None:
		file_path = self._file_for(day)
		with open(file_path, "w", encoding="utf-8") as f:
			json.dump(data, f, ensure_ascii=False, indent=2)

	def load_last_days(self, days: int) -> Dict[date, Dict[str, Any]]:
		result: Dict[date, Dict[str, Any]] = {}
		for i in range(days - 1, -1, -1):
			d = date.today() - timedelta(days=i)
			result[d] = self.load_day(d)
		return result

	def delete_day(self, d: date) -> None:
		file_path = self._file_for(d)
		try:
			if os.path.exists(file_path):
				os.remove(file_path)
		except Exception:
			pass

	def delete_last_days(self, n: int) -> None:
		for i in range(n):
			d = date.today() - timedelta(days=i)
			self.delete_day(d)

	def delete_all(self) -> None:
		try:
			for name in os.listdir(self.data_dir):
				if name.endswith(".json"):
					os.remove(os.path.join(self.data_dir, name))
		except Exception:
			pass


class AppState:
	"""Логика учёта по дням и категориям."""

	def __init__(self, storage: DayStorage) -> None:
		self.storage = storage
		self.day = date.today()
		raw = self.storage.load_day(self.day)
		self.sessions: List[WorkSession] = []
		for s in raw.get("sessions", []):
			self.sessions.append(
				WorkSession(
					start_iso=s.get("start_iso") or s.get("start") or s["start_iso"],
					end_iso=s.get("end_iso"),
					category=s.get("category", "trucker"),
				)
			)
		self.transactions: List[Transaction] = []
		for t in raw.get("transactions", []):
			self.transactions.append(
				Transaction(
					amount=t["amount"],
					type=t["type"],
					note=t.get("note", ""),
					time_iso=t.get("time_iso") or t.get("time") or t["time_iso"],
					category=t.get("category", "trucker"),
				)
			)

		self._running_index_by_category: Dict[str, Optional[int]] = {"trucker": None, "farm": None, "mine": None, "fish": None, "mushroom": None, "logger": None}
		for idx, s in enumerate(self.sessions):
			if s.end_iso is None and self._running_index_by_category.get(s.category) is None:
				self._running_index_by_category[s.category] = idx

	def start(self, category: str) -> None:
		if self._running_index_by_category.get(category) is not None:
			return
		session = WorkSession(start_iso=datetime.now().isoformat(timespec="seconds"), category=category)
		self.sessions.append(session)
		self._running_index_by_category[category] = len(self.sessions) - 1
		self._autosave()

	def stop(self, category: str) -> None:
		idx = self._running_index_by_category.get(category)
		if idx is None:
			return
		self.sessions[idx].end_iso = datetime.now().isoformat(timespec="seconds")
		self._running_index_by_category[category] = None
		self._autosave()

	def add_income(self, amount: int, note: str = "", category: str = "trucker") -> None:
		self._add_transaction(amount=abs(amount), ttype="income", note=note, category=category)

	def add_expense(self, amount: int, note: str = "", category: str = "trucker") -> None:
		self._add_transaction(amount=-abs(amount), ttype="expense", note=note, category=category)

	def _add_transaction(self, amount: int, ttype: str, note: str, category: str) -> None:
		self.transactions.append(Transaction(amount=amount, type=ttype, note=note, time_iso=datetime.now().isoformat(timespec="seconds"), category=category))
		self._autosave()

	def total_seconds(self, category: Optional[str] = None) -> int:
		seconds = 0
		for s in self.sessions:
			if category is not None and s.category != category:
				continue
			seconds += s.duration_seconds()
		return seconds

	def total_income(self, category: Optional[str] = None) -> int:
		return sum(t.amount for t in self.transactions if t.type == "income" and (category is None or t.category == category))

	def total_expense(self, category: Optional[str] = None) -> int:
		return -sum(t.amount for t in self.transactions if t.type == "expense" and (category is None or t.category == category))

	def net_profit(self, category: Optional[str] = None) -> int:
		return self.total_income(category) - self.total_expense(category)

	def profit_per_hour(self, category: Optional[str] = None) -> float:
		seconds = self.total_seconds(category)
		if seconds <= 0:
			return 0.0
		return self.net_profit(category) / (seconds / 3600.0)

	def current_session(self, category: str) -> Optional[WorkSession]:
		idx = self._running_index_by_category.get(category)
		return self.sessions[idx] if idx is not None else None

	def last_session(self, category: str) -> Optional[WorkSession]:
		for s in reversed(self.sessions):
			if s.category == category:
				return s
		return None

	def current_or_last_session(self, category: str) -> Optional[WorkSession]:
		return self.current_session(category) or self.last_session(category)

	def session_totals(self, session: WorkSession, category: Optional[str] = None) -> Tuple[int, int, int]:
		start_dt = datetime.fromisoformat(session.start_iso)
		end_dt = datetime.fromisoformat(session.end_iso) if session.end_iso else datetime.now()
		inc = 0
		exp = 0
		for t in self.transactions:
			if category is not None and t.category != category:
				continue
			time_dt = datetime.fromisoformat(t.time_iso)
			if start_dt <= time_dt <= end_dt:
				if t.type == "income":
					inc += t.amount
				else:
					exp += -t.amount
		return inc, exp, inc - exp

	def _autosave(self) -> None:
		data = {"sessions": [asdict(s) for s in self.sessions], "transactions": [asdict(t) for t in self.transactions]}
		self.storage.save_day(self.day, data)


# ------------------------
# Вспомогательные функции
# ------------------------

def format_seconds(total_seconds: int) -> str:
	hours = total_seconds // 3600
	minutes = (total_seconds % 3600) // 60
	seconds = total_seconds % 60
	return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_amount(text: str) -> Optional[int]:
	if not text:
		return None
	clean = text.strip().replace(" ", "").replace(",", ".")
	if not clean:
		return None
	try:
		value = float(clean)
		return int(value)
	except ValueError:
		return None


def parse_decimal(text: str) -> Optional[float]:
	if not text:
		return None
	clean = text.strip().replace(" ", "").replace(",", ".")
	if not clean:
		return None
	try:
		return float(clean)
	except ValueError:
		return None


def compute_day_series(sessions: List[WorkSession], transactions: List[Transaction]) -> Tuple[List[datetime], List[int], List[float]]:
	events: List[datetime] = [datetime.fromisoformat(t.time_iso) for t in transactions]
	if not events:
		return [], [], []
	events.sort()
	now_dt = datetime.now()
	if events[-1] < now_dt:
		events.append(now_dt)

	tx_sorted = sorted(transactions, key=lambda t: t.time_iso)
	tx_idx = 0
	cum_net = 0
	net_series: List[int] = []
	rph_series: List[float] = []
	for moment in events:
		while tx_idx < len(tx_sorted) and datetime.fromisoformat(tx_sorted[tx_idx].time_iso) <= moment:
			cum_net += tx_sorted[tx_idx].amount
			tx_idx += 1
		sec = 0
		for s in sessions:
			start_dt = datetime.fromisoformat(s.start_iso)
			end_dt = datetime.fromisoformat(s.end_iso) if s.end_iso else moment
			if moment <= start_dt:
				continue
			clip_end = min(end_dt, moment)
			if clip_end > start_dt:
				sec += int((clip_end - start_dt).total_seconds())
		rph = (cum_net / (sec / 3600.0)) if sec > 0 else 0.0
		net_series.append(cum_net)
		rph_series.append(rph)
	return events, net_series, rph_series


def compute_last_n_days(storage: DayStorage, n: int) -> Tuple[List[date], List[int], List[float]]:
	raw_days = storage.load_last_days(n)
	dates_list: List[date] = []
	net_per_day: List[int] = []
	rph_per_day: List[float] = []
	for d, raw in raw_days.items():
		sessions = [WorkSession(**s) for s in raw.get("sessions", [])]
		transactions = [Transaction(**t) for t in raw.get("transactions", [])]
		net = sum(t.amount for t in transactions)
		sec = 0
		for s in sessions:
			sec += WorkSession(**asdict(s)).duration_seconds()
		rph = (net / (sec / 3600.0)) if sec > 0 else 0.0
		dates_list.append(d)
		net_per_day.append(net)
		rph_per_day.append(rph)
	return dates_list, net_per_day, rph_per_day


# ------------------------
# UI — Дальнобойщик
# ------------------------
class TruckerTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state
		self.category = "trucker"

		self.work_time_label = QLabel("00:00:00")
		self.status_label = QLabel("Статус: Остановлено")

		self.start_button = QPushButton("Start")
		self.stop_button = QPushButton("Stop")
		self.stop_button.setEnabled(False)

		self.income_input = QLineEdit()
		self.income_input.setPlaceholderText("Сумма дохода, например 17000")
		self.income_input.setValidator(QIntValidator(0, 1_000_000_000))
		self.income_add_button = QPushButton("Добавить +")

		self.expense_input = QLineEdit()
		self.expense_input.setPlaceholderText("Сумма расхода, например 3000")
		self.expense_input.setValidator(QIntValidator(0, 1_000_000_000))
		self.expense_add_button = QPushButton("Добавить -")

		self.session_title_label = QLabel("Сессия: нет")
		self.session_income_label = QLabel("Доход: 0")
		self.session_expense_label = QLabel("Расход: 0")
		self.session_net_label = QLabel("Чистая прибыль: 0")

		self.total_income_label = QLabel("Доход: 0")
		self.total_expense_label = QLabel("Расход: 0")
		self.net_profit_label = QLabel("Чистая прибыль: 0")
		self.rate_hour_label = QLabel("Заработок в час: 0.00")

		self._build_layout()
		self._connect()

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self._tick)
		self._refresh_all()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		time_group = QGroupBox("Рабочее время")
		time_layout = QVBoxLayout()
		time_layout.addWidget(self.work_time_label)
		time_layout.addWidget(self.status_label)
		btns = QHBoxLayout()
		btns.addWidget(self.start_button)
		btns.addWidget(self.stop_button)
		time_layout.addLayout(btns)
		time_group.setLayout(time_layout)

		money_group = QGroupBox("Доходы и расходы")
		form = QFormLayout()
		inc_row = QHBoxLayout()
		inc_row.addWidget(self.income_input)
		inc_row.addWidget(self.income_add_button)
		form.addRow("Доход (+):", inc_row)
		exp_row = QHBoxLayout()
		exp_row.addWidget(self.expense_input)
		exp_row.addWidget(self.expense_add_button)
		form.addRow("Расход (-):", exp_row)
		money_group.setLayout(form)

		sess_group = QGroupBox("Итого за сессию")
		sess_layout = QVBoxLayout()
		sess_layout.addWidget(self.session_title_label)
		sess_layout.addWidget(self.session_income_label)
		sess_layout.addWidget(self.session_expense_label)
		sess_layout.addWidget(self.session_net_label)
		sess_group.setLayout(sess_layout)

		totals = QGroupBox("Итого за день")
		totals_layout = QVBoxLayout()
		totals_layout.addWidget(self.total_income_label)
		totals_layout.addWidget(self.total_expense_label)
		totals_layout.addWidget(self.net_profit_label)
		totals_layout.addWidget(self.rate_hour_label)
		totals.setLayout(totals_layout)

		root.addWidget(time_group)
		root.addWidget(money_group)
		root.addWidget(sess_group)
		root.addWidget(totals)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.start_button.clicked.connect(self._on_start)
		self.stop_button.clicked.connect(self._on_stop)
		self.income_add_button.clicked.connect(self._on_add_income)
		self.expense_add_button.clicked.connect(self._on_add_expense)

	def _on_start(self) -> None:
		self.state.start(self.category)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.status_label.setText("Статус: Идёт работа")
		self.timer.start()
		self._refresh_totals()

	def _on_stop(self) -> None:
		self.state.stop(self.category)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)
		self.status_label.setText("Статус: Остановлено")
		self.timer.stop()
		self._refresh_totals()
		self._refresh_time()

	def _on_add_income(self) -> None:
		amount = parse_amount(self.income_input.text())
		if amount is None or amount <= 0:
			QMessageBox.warning(self, "Ошибка", "Введите корректную сумму дохода")
			return
		self.state.add_income(amount, category=self.category)
		self.income_input.clear()
		self._refresh_totals()

	def _on_add_expense(self) -> None:
		amount = parse_amount(self.expense_input.text())
		if amount is None or amount <= 0:
			QMessageBox.warning(self, "Ошибка", "Введите корректную сумму расхода")
			return
		self.state.add_expense(amount, category=self.category)
		self.expense_input.clear()
		self._refresh_totals()

	def _tick(self) -> None:
		self._refresh_time()
		self._refresh_session_totals()

	def _refresh_time(self) -> None:
		self.work_time_label.setText(format_seconds(self.state.total_seconds(self.category)))

	def _refresh_session_totals(self) -> None:
		session = self.state.current_or_last_session(self.category)
		if session is None:
			self.session_title_label.setText("Сессия: нет")
			self.session_income_label.setText("Доход: 0")
			self.session_expense_label.setText("Расход: 0")
			self.session_net_label.setText("Чистая прибыль: 0")
			return
		is_running = session.end_iso is None
		title = "Текущая сессия" if is_running else "Последняя сессия"
		inc, exp, net = self.state.session_totals(session, category=self.category)
		self.session_title_label.setText(title)
		self.session_income_label.setText(f"Доход: {inc:,}".replace(",", " "))
		self.session_expense_label.setText(f"Расход: {exp:,}".replace(",", " "))
		self.session_net_label.setText(f"Чистая прибыль: {net:,}".replace(",", " "))

	def _refresh_totals(self) -> None:
		self.total_income_label.setText(f"Доход: {self.state.total_income(self.category):,}".replace(",", " "))
		self.total_expense_label.setText(f"Расход: {self.state.total_expense(self.category):,}".replace(",", " "))
		self.net_profit_label.setText(f"Чистая прибыль: {self.state.net_profit(self.category):,}".replace(",", " "))
		self.rate_hour_label.setText(f"Заработок в час: {self.state.profit_per_hour(self.category):.2f}")
		self._refresh_session_totals()

	def _refresh_all(self) -> None:
		is_running = self.state.current_session(self.category) is not None
		self.start_button.setEnabled(not is_running)
		self.stop_button.setEnabled(is_running)
		self.status_label.setText("Статус: Идёт работа" if is_running else "Статус: Остановлено")
		if is_running:
			self.timer.start()
		else:
			self.timer.stop()
		self._refresh_totals()
		self._refresh_time()


# ------------------------
# UI — Ферма
# ------------------------
class FarmTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state
		self.category = "farm"

		self.work_time_label = QLabel("00:00:00")
		self.status_label = QLabel("Статус: Остановлено")

		self.start_button = QPushButton("Start")
		self.stop_button = QPushButton("Stop")
		self.stop_button.setEnabled(False)

		# Поля фермы
		self.seed_qty_input = QLineEdit()
		self.seed_qty_input.setPlaceholderText("Кол-во семян, шт")
		self.seed_qty_input.setValidator(QIntValidator(0, 1_000_000_000))

		self.seed_price_input = QLineEdit()
		self.seed_price_input.setPlaceholderText("Цена 1 семечки")
		self.seed_price_input.setValidator(QIntValidator(0, 1_000_000_000))

		self.sale_price_input = QLineEdit()
		self.sale_price_input.setPlaceholderText("Цена продажи за 1 шт")
		self.sale_price_input.setValidator(QIntValidator(0, 1_000_000_000))

		self.sale_qty_input = QLineEdit()
		self.sale_qty_input.setPlaceholderText("Сколько шт продаем")
		self.sale_qty_input.setValidator(QIntValidator(0, 1_000_000_000))

		self.add_sale_button = QPushButton("Добавить продажу")

		self.session_title_label = QLabel("Сессия: нет")
		self.session_income_label = QLabel("Доход: 0")
		self.session_expense_label = QLabel("Расход: 0")
		self.session_net_label = QLabel("Чистая прибыль: 0")

		self.total_income_label = QLabel("Доход: 0")
		self.total_expense_label = QLabel("Расход: 0")
		self.net_profit_label = QLabel("Чистая прибыль: 0")
		self.rate_hour_label = QLabel("Заработок в час: 0.00")

		self._build_layout()
		self._connect()

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self._tick)
		self._refresh_all()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		time_group = QGroupBox("Рабочее время")
		time_layout = QVBoxLayout()
		time_layout.addWidget(self.work_time_label)
		time_layout.addWidget(self.status_label)
		btns = QHBoxLayout()
		btns.addWidget(self.start_button)
		btns.addWidget(self.stop_button)
		time_layout.addLayout(btns)
		time_group.setLayout(time_layout)

		farm_group = QGroupBox("Параметры продажи")
		form = QFormLayout()
		row1 = QHBoxLayout()
		row1.addWidget(self.seed_qty_input)
		row1.addWidget(self.seed_price_input)
		form.addRow("Семена (шт / цена):", row1)
		row2 = QHBoxLayout()
		row2.addWidget(self.sale_qty_input)
		row2.addWidget(self.sale_price_input)
		form.addRow("Продажа (шт / цена):", row2)
		row3 = QHBoxLayout()
		row3.addStretch(1)
		row3.addWidget(self.add_sale_button)
		form.addRow("", row3)
		farm_group.setLayout(form)

		sess_group = QGroupBox("Итого за сессию")
		sess_layout = QVBoxLayout()
		sess_layout.addWidget(self.session_title_label)
		sess_layout.addWidget(self.session_income_label)
		sess_layout.addWidget(self.session_expense_label)
		sess_layout.addWidget(self.session_net_label)
		sess_group.setLayout(sess_layout)

		totals = QGroupBox("Итого за день")
		totals_layout = QVBoxLayout()
		totals_layout.addWidget(self.total_income_label)
		totals_layout.addWidget(self.total_expense_label)
		totals_layout.addWidget(self.net_profit_label)
		totals_layout.addWidget(self.rate_hour_label)
		totals.setLayout(totals_layout)

		root.addWidget(time_group)
		root.addWidget(farm_group)
		root.addWidget(sess_group)
		root.addWidget(totals)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.start_button.clicked.connect(self._on_start)
		self.stop_button.clicked.connect(self._on_stop)
		self.add_sale_button.clicked.connect(self._on_add_sale)

	def _on_start(self) -> None:
		self.state.start(self.category)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.status_label.setText("Статус: Идёт работа")
		self.timer.start()
		self._refresh_totals()

	def _on_stop(self) -> None:
		self.state.stop(self.category)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)
		self.status_label.setText("Статус: Остановлено")
		self.timer.stop()
		self._refresh_totals()
		self._refresh_time()

	def _on_add_sale(self) -> None:
		seed_qty = parse_amount(self.seed_qty_input.text()) or 0
		seed_price = parse_amount(self.seed_price_input.text()) or 0
		sale_qty = parse_amount(self.sale_qty_input.text()) or 0
		sale_price = parse_amount(self.sale_price_input.text()) or 0
		if seed_qty < 0 or seed_price < 0 or sale_qty <= 0 or sale_price < 0:
			QMessageBox.warning(self, "Ошибка", "Проверьте введённые значения")
			return
		seed_cost = seed_qty * seed_price
		sale_income = sale_qty * sale_price
		if seed_cost > 0:
			self.state.add_expense(seed_cost, note="Семена", category=self.category)
		if sale_income > 0:
			self.state.add_income(sale_income, note="Продажа", category=self.category)
		self.seed_qty_input.clear()
		self.seed_price_input.clear()
		self.sale_qty_input.clear()
		self.sale_price_input.clear()
		self._refresh_totals()

	def _tick(self) -> None:
		self._refresh_time()
		self._refresh_session_totals()

	def _refresh_time(self) -> None:
		self.work_time_label.setText(format_seconds(self.state.total_seconds(self.category)))

	def _refresh_session_totals(self) -> None:
		session = self.state.current_or_last_session(self.category)
		if session is None:
			self.session_title_label.setText("Сессия: нет")
			self.session_income_label.setText("Доход: 0")
			self.session_expense_label.setText("Расход: 0")
			self.session_net_label.setText("Чистая прибыль: 0")
			return
		is_running = session.end_iso is None
		title = "Текущая сессия" if is_running else "Последняя сессия"
		inc, exp, net = self.state.session_totals(session, category=self.category)
		self.session_title_label.setText(title)
		self.session_income_label.setText(f"Доход: {inc:,}".replace(",", " "))
		self.session_expense_label.setText(f"Расход: {exp:,}".replace(",", " "))
		self.session_net_label.setText(f"Чистая прибыль: {net:,}".replace(",", " "))

	def _refresh_totals(self) -> None:
		self.total_income_label.setText(f"Доход: {self.state.total_income(self.category):,}".replace(",", " "))
		self.total_expense_label.setText(f"Расход: {self.state.total_expense(self.category):,}".replace(",", " "))
		self.net_profit_label.setText(f"Чистая прибыль: {self.state.net_profit(self.category):,}".replace(",", " "))
		self.rate_hour_label.setText(f"Заработок в час: {self.state.profit_per_hour(self.category):.2f}")
		self._refresh_session_totals()

	def _refresh_all(self) -> None:
		is_running = self.state.current_session(self.category) is not None
		self.start_button.setEnabled(not is_running)
		self.stop_button.setEnabled(is_running)
		self.status_label.setText("Статус: Идёт работа" if is_running else "Статус: Остановлено")
		if is_running:
			self.timer.start()
		else:
			self.timer.stop()
		self._refresh_totals()
		self._refresh_time()


# ------------------------
# UI — Карьер
# ------------------------
class MineTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state
		self.category = "mine"

		self.work_time_label = QLabel("00:00:00")
		self.status_label = QLabel("Статус: Остановлено")

		self.start_button = QPushButton("Start")
		self.stop_button = QPushButton("Stop")
		self.stop_button.setEnabled(False)

		self.ores = [
			"Железная",
			"Серебряная",
			"Медная",
			"Оловянная",
			"Золотая",
			"Марганцевая",
			"Кремниевая",
			"Хромовая",
			"Никелевая",
		]
		self.qty_inputs: Dict[str, QLineEdit] = {}
		self.price_inputs: Dict[str, QLineEdit] = {}
		self.add_sales_button = QPushButton("Добавить продажи")

		self.session_title_label = QLabel("Сессия: нет")
		self.session_income_label = QLabel("Доход: 0")
		self.session_expense_label = QLabel("Расход: 0")
		self.session_net_label = QLabel("Чистая прибыль: 0")

		self.total_income_label = QLabel("Доход: 0")
		self.total_expense_label = QLabel("Расход: 0")
		self.net_profit_label = QLabel("Чистая прибыль: 0")
		self.rate_hour_label = QLabel("Заработок в час: 0.00")

		self._build_layout()
		self._connect()

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self._tick)
		self._refresh_all()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		time_group = QGroupBox("Рабочее время")
		time_layout = QVBoxLayout()
		time_layout.addWidget(self.work_time_label)
		time_layout.addWidget(self.status_label)
		btns = QHBoxLayout()
		btns.addWidget(self.start_button)
		btns.addWidget(self.stop_button)
		time_layout.addLayout(btns)
		time_group.setLayout(time_layout)

		ore_group = QGroupBox("Продажа руды за сессию")
		form = QFormLayout()
		for name in self.ores:
			row = QHBoxLayout()
			qty = QLineEdit()
			qty.setPlaceholderText("шт")
			qty.setValidator(QIntValidator(0, 1_000_000_000))
			price = QLineEdit()
			price.setPlaceholderText("цена за 1")
			price.setValidator(QIntValidator(0, 1_000_000_000))
			row.addWidget(qty)
			row.addWidget(price)
			form.addRow(f"{name}:", row)
			self.qty_inputs[name] = qty
			self.price_inputs[name] = price
		row_btn = QHBoxLayout()
		row_btn.addStretch(1)
		row_btn.addWidget(self.add_sales_button)
		form.addRow("", row_btn)
		ore_group.setLayout(form)

		sess_group = QGroupBox("Итого за сессию")
		sess_layout = QVBoxLayout()
		sess_layout.addWidget(self.session_title_label)
		sess_layout.addWidget(self.session_income_label)
		sess_layout.addWidget(self.session_expense_label)
		sess_layout.addWidget(self.session_net_label)
		sess_group.setLayout(sess_layout)

		totals = QGroupBox("Итого за день")
		totals_layout = QVBoxLayout()
		totals_layout.addWidget(self.total_income_label)
		totals_layout.addWidget(self.total_expense_label)
		totals_layout.addWidget(self.net_profit_label)
		totals_layout.addWidget(self.rate_hour_label)
		totals.setLayout(totals_layout)

		root.addWidget(time_group)
		root.addWidget(ore_group)
		root.addWidget(sess_group)
		root.addWidget(totals)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.start_button.clicked.connect(self._on_start)
		self.stop_button.clicked.connect(self._on_stop)
		self.add_sales_button.clicked.connect(self._on_add_sales)

	def _on_start(self) -> None:
		self.state.start(self.category)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.status_label.setText("Статус: Идёт работа")
		self.timer.start()
		self._refresh_totals()

	def _on_stop(self) -> None:
		self.state.stop(self.category)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)
		self.status_label.setText("Статус: Остановлено")
		self.timer.stop()
		self._refresh_totals(); self._refresh_time()

	def _on_add_sales(self) -> None:
		total_income = 0
		for name in self.ores:
			qty = parse_amount(self.qty_inputs[name].text()) or 0
			price = parse_amount(self.price_inputs[name].text()) or 0
			if qty > 0 and price >= 0:
				income = qty * price
				total_income += income
				self.state.add_income(income, note=f"Продажа ({name})", category=self.category)
				self.qty_inputs[name].clear()
				self.price_inputs[name].clear()
		if total_income == 0:
			QMessageBox.information(self, "Продажи", "Нет валидных значений для добавления")
		self._refresh_totals()

	def _tick(self) -> None:
		self._refresh_time()
		self._refresh_session_totals()

	def _refresh_time(self) -> None:
		self.work_time_label.setText(format_seconds(self.state.total_seconds(self.category)))

	def _refresh_session_totals(self) -> None:
		session = self.state.current_or_last_session(self.category)
		if session is None:
			self.session_title_label.setText("Сессия: нет")
			self.session_income_label.setText("Доход: 0")
			self.session_expense_label.setText("Расход: 0")
			self.session_net_label.setText("Чистая прибыль: 0")
			return
		is_running = session.end_iso is None
		title = "Текущая сессия" if is_running else "Последняя сессия"
		inc, exp, net = self.state.session_totals(session, category=self.category)
		self.session_title_label.setText(title)
		self.session_income_label.setText(f"Доход: {inc:,}".replace(",", " "))
		self.session_expense_label.setText(f"Расход: {exp:,}".replace(",", " "))
		self.session_net_label.setText(f"Чистая прибыль: {net:,}".replace(",", " "))

	def _refresh_totals(self) -> None:
		self.total_income_label.setText(f"Доход: {self.state.total_income(self.category):,}".replace(",", " "))
		self.total_expense_label.setText(f"Расход: {self.state.total_expense(self.category):,}".replace(",", " "))
		self.net_profit_label.setText(f"Чистая прибыль: {self.state.net_profit(self.category):,}".replace(",", " "))
		self.rate_hour_label.setText(f"Заработок в час: {self.state.profit_per_hour(self.category):.2f}")
		self._refresh_session_totals()

	def _refresh_all(self) -> None:
		is_running = self.state.current_session(self.category) is not None
		self.start_button.setEnabled(not is_running)
		self.stop_button.setEnabled(is_running)
		self.status_label.setText("Статус: Идёт работа" if is_running else "Статус: Остановлено")
		if is_running:
			self.timer.start()
		else:
			self.timer.stop()
		self._refresh_totals(); self._refresh_time()


# ------------------------
# UI — Рыбалка
# ------------------------
class FishTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state
		self.category = "fish"

		self.work_time_label = QLabel("00:00:00")
		self.status_label = QLabel("Статус: Остановлено")
		self.start_button = QPushButton("Start")
		self.stop_button = QPushButton("Stop")
		self.stop_button.setEnabled(False)

		# Уровни 1..9 и тестовые рыбы (редактируйте список и цены)
		self.fish_levels: Dict[int, List[Dict[str, Any]]] = {
			1: [
				{"name": "Красноперка", "market_price": "0.96-1.35"},
				{"name": "Лещ", "market_price": "0.59-0.83"},
				{"name": "Плотва", "market_price": "0.89-1.25"},
				{"name": "Вобла", "market_price": "0.63-0.88"},
			],
			2: [
				{"name": "Коричневый Сом", "market_price": "0.73-1.02"},
				{"name": "Серебряный Карась", "market_price": "0.78-1.09"},
				{"name": "Вобла", "market_price": "0.63-0.88"},
				{"name": "Краснопёрка", "market_price": "0.96-1.35"},
				{"name": "Лещ", "market_price": "0.59-0.83"},
				{"name": "Плотва", "market_price": "0.89-1.25"},
			],
			3: [
				{"name": "Речной Окунь", "market_price": "0.50-0.70"},
				{"name": "Обыкновенная щука", "market_price": "0.44-0.62"},
				{"name": "Радужная форель", "market_price": "0.45-0.63"},
				{"name": "Зеркальный карп", "market_price": "0.39-0.54"},
				{"name": "Сом обыкновенный", "market_price": "0.39-0.55"},
				{"name": "Коричневый Сом", "market_price": "0.73-1.02"},
				{"name": "Серебряный Карась", "market_price": "0.78-1.09"},
			],
			4: [
				{"name": "Сазан", "market_price": "0.44-0.62"},
				{"name": "Судак Обыкновенный", "market_price": "0.51-0.72"},
				{"name": "Голавль", "market_price": "0.50-0.70"},
				{"name": "Речной Окунь", "market_price": "0.50-0.70"},
				{"name": "Обыкновенная щука", "market_price": "0.44-0.62"},
				{"name": "Радужная форель", "market_price": "0.45-0.63"},
				{"name": "Зеркальный карп", "market_price": "0.39-0.54"},
				{"name": "Сом обыкновенный", "market_price": "0.39-0.55"},
				{"name": "Серебряный Карась", "market_price": "0.78-1.09"},
			],
			5: [
				{"name": "Жерех", "market_price": "0.53-0.74"},
				{"name": "Стерлядь(запррещённая)", "market_price": "0.55-0.77"},
				{"name": "Сазан", "market_price": "0.44-0.62"},
				{"name": "Судак", "market_price": "0.51-0.72"},
				{"name": "Голавль", "market_price": "0.50-0.70"},
				{"name": "Речной Окунь", "market_price": "0.50-0.70"},
				{"name": "Обыкновенная щука", "market_price": "0.44-0.62"},
				{"name": "Радужная форель", "market_price": "0.45-0.63"},
				{"name": "Зеркальный карп", "market_price": "0.39-0.54"},
				{"name": "Сом обыкновенный", "market_price": "0.39-0.55"},
				{"name": "Серебряный Карась", "market_price": "0.78-1.09"},
			],
			6: [
				{"name": "Прибрежный басс", "market_price": "0.48-0.67"},
				{"name": "Альбула", "market_price": "0.51-0.71"},
				{"name": "Снук Обыкновенный", "market_price": "0.46-0.65"},
				{"name": "Полосатый Лаврак", "market_price": "0.52-0.73"},
				{"name": "Стальноголовый лосось", "market_price": "0.40-0.56"},
			],
			7: [
				{"name": "Барракуда", "market_price": "0.49-0.69"},
				{"name": "Круглый Трахинот", "market_price": "0.44-0.62"},
				{"name": "Темный Горбыль", "market_price": "0.43-0.60"},
				{"name": "Прибрежный басс", "market_price": "0.48-0.67"},
				{"name": "Альбула", "market_price": "0.51-0.71"},
				{"name": "Снук Обыкновенный", "market_price": "0.46-0.65"},
				{"name": "Полосатый Лаврак", "market_price": "0.52-0.73"},
			],
			8: [
				{"name": "Красный Горбыль", "market_price": "0,46-0,65"},
				{"name": "Тарпон", "market_price": "0,45-0,63"},
				{"name": "Марлин", "market_price": "0,51-0,72"},
				{"name": "Барракуда", "market_price": "0,49-0,69"},
				{"name": "Круглый Трахинот", "market_price": "0,44-0,62"},
				{"name": "Темный Горбыль", "market_price": "0,43-0,60"},
			],
			9: [
				{"name": "Сериолла(запрщённая)", "market_price": "0,68-0,95"},
				{"name": "Рустер(запрщённая)", "market_price": "0,64-0,90"},
				{"name": "Красный Горбыль", "market_price": "0,46-0,65"},
				{"name": "Тарпон", "market_price": "0,45-0,63"},
				{"name": "Марлин", "market_price": "0,51-0,72"},
			],
		}

		self.qty_inputs_by_level: Dict[int, Dict[str, QLineEdit]] = {}
		self.price_inputs_by_level: Dict[int, Dict[str, QLineEdit]] = {}

		self.levels_tabs = QTabWidget()
		for lvl in range(1, 10):
			page = QWidget()
			form = QFormLayout()
			self.qty_inputs_by_level[lvl] = {}
			self.price_inputs_by_level[lvl] = {}
			for fish in self.fish_levels[lvl]:
				row = QHBoxLayout()
				qty = QLineEdit(); qty.setPlaceholderText("грамм"); qty.setValidator(QIntValidator(0, 1_000_000_000))
				price = QLineEdit(); price.setPlaceholderText("цена за 1 г")
				price.setValidator(QRegularExpressionValidator(QRegularExpression(r"^[0-9]+([\.,][0-9]{0,4})?$")))
				market = QLabel(f"Скупщик: {fish['market_price']} за 1 г")
				row.addWidget(qty); row.addWidget(price); row.addWidget(market)
				form.addRow(f"{fish['name']}:", row)
				self.qty_inputs_by_level[lvl][fish['name']] = qty
				self.price_inputs_by_level[lvl][fish['name']] = price
			btn_row = QHBoxLayout()
			add_btn = QPushButton("Добавить продажи уровня")
			add_btn.clicked.connect(lambda _=False, l=lvl: self._on_add_sales_level(l))
			btn_row.addStretch(1); btn_row.addWidget(add_btn)
			form.addRow("", btn_row)
			page.setLayout(form)
			self.levels_tabs.addTab(page, f"Ур. {lvl}")

		self.session_title_label = QLabel("Сессия: нет")
		self.session_income_label = QLabel("Доход: 0")
		self.session_expense_label = QLabel("Расход: 0")
		self.session_net_label = QLabel("Чистая прибыль: 0")

		self.total_income_label = QLabel("Доход: 0")
		self.total_expense_label = QLabel("Расход: 0")
		self.net_profit_label = QLabel("Чистая прибыль: 0")
		self.rate_hour_label = QLabel("Заработок в час: 0.00")

		self._build_layout()
		self._connect()

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self._tick)
		self._refresh_all()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		time_group = QGroupBox("Рабочее время")
		time_layout = QVBoxLayout()
		time_layout.addWidget(self.work_time_label)
		time_layout.addWidget(self.status_label)
		btns = QHBoxLayout(); btns.addWidget(self.start_button); btns.addWidget(self.stop_button)
		time_layout.addLayout(btns)
		time_group.setLayout(time_layout)

		sess_group = QGroupBox("Итого за сессию")
		sess_layout = QVBoxLayout()
		sess_layout.addWidget(self.session_title_label)
		sess_layout.addWidget(self.session_income_label)
		sess_layout.addWidget(self.session_expense_label)
		sess_layout.addWidget(self.session_net_label)
		sess_group.setLayout(sess_layout)

		totals = QGroupBox("Итого за день")
		totals_layout = QVBoxLayout()
		totals_layout.addWidget(self.total_income_label)
		totals_layout.addWidget(self.total_expense_label)
		totals_layout.addWidget(self.net_profit_label)
		totals_layout.addWidget(self.rate_hour_label)
		totals.setLayout(totals_layout)

		root.addWidget(time_group)
		root.addWidget(self.levels_tabs)
		root.addWidget(sess_group)
		root.addWidget(totals)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.start_button.clicked.connect(self._on_start)
		self.stop_button.clicked.connect(self._on_stop)

	def _on_start(self) -> None:
		self.state.start(self.category)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.status_label.setText("Статус: Идёт работа")
		self.timer.start()
		self._refresh_totals()

	def _on_stop(self) -> None:
		self.state.stop(self.category)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)
		self.status_label.setText("Статус: Остановлено")
		self.timer.stop()
		self._refresh_totals(); self._refresh_time()

	def _on_add_sales_level(self, level: int) -> None:
		total_income = 0
		for name, qty_input in self.qty_inputs_by_level[level].items():
			grams = parse_amount(qty_input.text()) or 0
			price_per_gram = parse_decimal(self.price_inputs_by_level[level][name].text()) or 0.0
			if grams > 0 and price_per_gram >= 0:
				income = int(grams * price_per_gram)
				total_income += income
				self.state.add_income(income, note=f"Рыба {name} (L{level}) {grams} г", category=self.category)
				qty_input.clear(); self.price_inputs_by_level[level][name].clear()
		if total_income == 0:
			QMessageBox.information(self, "Продажи", "Нет валидных значений для добавления")
		self._refresh_totals()

	def _tick(self) -> None:
		self._refresh_time(); self._refresh_session_totals()

	def _refresh_time(self) -> None:
		self.work_time_label.setText(format_seconds(self.state.total_seconds(self.category)))

	def _refresh_session_totals(self) -> None:
		session = self.state.current_or_last_session(self.category)
		if session is None:
			self.session_title_label.setText("Сессия: нет")
			self.session_income_label.setText("Доход: 0")
			self.session_expense_label.setText("Расход: 0")
			self.session_net_label.setText("Чистая прибыль: 0")
			return
		is_running = session.end_iso is None
		title = "Текущая сессия" if is_running else "Последняя сессия"
		inc, exp, net = self.state.session_totals(session, category=self.category)
		self.session_title_label.setText(title)
		self.session_income_label.setText(f"Доход: {inc:,}".replace(",", " "))
		self.session_expense_label.setText(f"Расход: {exp:,}".replace(",", " "))
		self.session_net_label.setText(f"Чистая прибыль: {net:,}".replace(",", " "))

	def _refresh_totals(self) -> None:
		self.total_income_label.setText(f"Доход: {self.state.total_income(self.category):,}".replace(",", " "))
		self.total_expense_label.setText(f"Расход: {self.state.total_expense(self.category):,}".replace(",", " "))
		self.net_profit_label.setText(f"Чистая прибыль: {self.state.net_profit(self.category):,}".replace(",", " "))
		self.rate_hour_label.setText(f"Заработок в час: {self.state.profit_per_hour(self.category):.2f}")
		self._refresh_session_totals()

	def _refresh_all(self) -> None:
		is_running = self.state.current_session(self.category) is not None
		self.start_button.setEnabled(not is_running)
		self.stop_button.setEnabled(is_running)
		self.status_label.setText("Статус: Идёт работа" if is_running else "Статус: Остановлено")
		if is_running:
			self.timer.start()
		else:
			self.timer.stop()
		self._refresh_totals(); self._refresh_time()


# ------------------------
# UI — Статистика
# ------------------------
class StatsTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state

		self.time_label = QLabel("00:00:00")
		self.net_label = QLabel("Чистая прибыль: 0")
		self.rph_label = QLabel("Заработок в час: 0.00")

		self.period_tabs = QTabWidget()
		self.period_tabs.addTab(QWidget(), "1 день")
		self.period_tabs.addTab(QWidget(), "7 дней")
		self.period_tabs.addTab(QWidget(), "30 дней")

		self.reset_button = QPushButton("Сброс…")
		self.reset_menu = QMenu(self)
		self._add_reset_actions()
		self.reset_button.setMenu(self.reset_menu)

		self.figure = Figure(figsize=(5, 3), tight_layout=True)
		self.canvas = FigureCanvas(self.figure)

		header = QHBoxLayout()
		header.addWidget(QLabel("Статистика"))
		header.addStretch(1)
		header.addWidget(self.reset_button)

		layout = QVBoxLayout()
		layout.addLayout(header)
		layout.addWidget(QLabel(f"Дата: {date.today().strftime('%d.%m.%Y')}"))
		layout.addWidget(QLabel("Общее рабочее время:"))
		layout.addWidget(self.time_label)
		layout.addWidget(self.net_label)
		layout.addWidget(self.rph_label)
		layout.addWidget(self.period_tabs)
		layout.addWidget(self.canvas)
		layout.addWidget(QLabel("Сводка по категориям"))
		# Заменяем скролл на вкладки по категориям
		self.summary_tabs = QTabWidget()
		layout.addWidget(self.summary_tabs)
		layout.addStretch(1)
		self.setLayout(layout)

		self.period_tabs.currentChanged.connect(self._on_period_changed)

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self.refresh)
		self.timer.start()
		self.refresh()

	def _add_reset_actions(self) -> None:
		act_today = self.reset_menu.addAction("Сегодня")
		act_7 = self.reset_menu.addAction("Последние 7 дней")
		act_30 = self.reset_menu.addAction("Последние 30 дней")
		self.reset_menu.addSeparator()
		act_all = self.reset_menu.addAction("Все данные")
		act_today.triggered.connect(lambda: self._confirm_and_reset("today"))
		act_7.triggered.connect(lambda: self._confirm_and_reset("7"))
		act_30.triggered.connect(lambda: self._confirm_and_reset("30"))
		act_all.triggered.connect(lambda: self._confirm_and_reset("all"))

	def _clear_state(self) -> None:
		self.state.sessions = []
		self.state.transactions = []
		self.state._running_index_by_category = {"trucker": None, "farm": None, "mine": None, "fish": None, "mushroom": None, "logger": None}

	def _confirm_and_reset(self, scope: str) -> None:
		map_title = {
			"today": "Сбросить статистику за сегодня?",
			"7": "Сбросить статистику за последние 7 дней (включая сегодня)?",
			"30": "Сбросить статистику за последние 30 дней (включая сегодня)?",
			"all": "Удалить ВСЕ данные?",
		}
		ret = QMessageBox.question(self, "Подтверждение", map_title[scope])
		if ret != QMessageBox.StandardButton.Yes:
			return
		if scope == "today":
			self._clear_state()
			self.state.storage.delete_day(date.today())
		elif scope == "7":
			self._clear_state()
			self.state.storage.delete_last_days(7)
		elif scope == "30":
			self._clear_state()
			self.state.storage.delete_last_days(30)
		else:
			self._clear_state()
			self.state.storage.delete_all()
		self.state._autosave()
		self.refresh()

	def refresh(self) -> None:
		self.time_label.setText(format_seconds(self.state.total_seconds()))
		self.net_label.setText(f"Чистая прибыль: {self.state.net_profit():,}".replace(",", " "))
		self.rph_label.setText(f"Заработок в час: {self.state.profit_per_hour():.2f}")
		self.replot()
		# Сводку пересчитываем по активному периоду
		index = self.period_tabs.currentIndex()
		self._build_summary_tabs(1 if index == 0 else (7 if index == 1 else 30))

	def replot(self) -> None:
		index = self.period_tabs.currentIndex()
		self.figure.clear()
		ax_left = self.figure.add_subplot(111)
		ax_right = ax_left.twinx()
		ax_left.margins(y=0.3)
		ax_right.margins(y=0.3)

		if index == 0:
			times, net, rph = compute_day_series(self.state.sessions, self.state.transactions)
			if times:
				ax_left.plot(times, net, color="tab:blue", marker="o", label="Чистая прибыль (день)")
				ax_right.plot(times, rph, color="tab:red", marker="o", label="Зар/час")
				ax_left.set_xlabel("Время")
			else:
				ax_left.text(0.5, 0.5, "Нет данных", transform=ax_left.transAxes, ha="center")
		elif index == 1:
			ds, net, rph = compute_last_n_days(self.state.storage, 7)
			ax_left.plot(ds, net, color="tab:blue", marker="o", label="Чистая прибыль (день)")
			ax_right.plot(ds, rph, color="tab:red", marker="o", label="Зар/час")
			ax_left.set_xlabel("Дни")
		else:
			ds, net, rph = compute_last_n_days(self.state.storage, 30)
			ax_left.plot(ds, net, color="tab:blue", marker="o", label="Чистая прибыль (день)")
			ax_right.plot(ds, rph, color="tab:red", marker="o", label="Зар/час")
			ax_left.set_xlabel("Дни")

		ax_left.set_ylabel("Чистая прибыль", color="tab:blue")
		ax_right.set_ylabel("Зар/час", color="tab:red")
		ax_left.tick_params(axis='y', colors='tab:blue')
		ax_right.tick_params(axis='y', colors='tab:red')
		ax_left.grid(True, linestyle=":", alpha=0.5)
		ax_left.legend(loc='upper left')
		ax_right.legend(loc='upper right')
		self.figure.autofmt_xdate()
		self.canvas.draw_idle()

	def _build_summary_tabs(self, days: int) -> None:
		# запомним текущую вкладку по тексту
		current_text = None
		if self.summary_tabs.count() > 0 and self.summary_tabs.currentIndex() >= 0:
			current_text = self.summary_tabs.tabText(self.summary_tabs.currentIndex())
		self.summary_tabs.clear()

		# Агрегация по категориям
		cat_to_net: Dict[str, int] = {}
		if days <= 1:
			for t in self.state.transactions:
				cat_to_net[t.category] = cat_to_net.get(t.category, 0) + t.amount
		else:
			raw_days = self.state.storage.load_last_days(days)
			for _d, raw in raw_days.items():
				for t in raw.get("transactions", []):
					cat = t.get("category", "trucker")
					amt = int(t.get("amount", 0))
					cat_to_net[cat] = cat_to_net.get(cat, 0) + amt

		labels = {
			"trucker": "Дальнобойщик",
			"farm": "Ферма",
			"mine": "Карьер",
			"fish": "Рыбалка",
			"mushroom": "Грибник",
			"logger": "Лесоруб",
		}
		added_titles: List[str] = []
		if not cat_to_net:
			page = QWidget(); v = QVBoxLayout(); v.addWidget(QLabel("Нет данных")); v.addStretch(1); page.setLayout(v)
			self.summary_tabs.addTab(page, "Все")
			return
		for cat, total in cat_to_net.items():
			page = QWidget(); v = QVBoxLayout();
			v.addWidget(QLabel(f"Чистая прибыль: {total:,}".replace(",", " ")))
			v.addStretch(1); page.setLayout(v)
			title = labels.get(cat, cat)
			self.summary_tabs.addTab(page, title)
			added_titles.append(title)
		# восстановим выбор, если возможно
		if current_text and current_text in added_titles:
			for i in range(self.summary_tabs.count()):
				if self.summary_tabs.tabText(i) == current_text:
					self.summary_tabs.setCurrentIndex(i)
					break

	def _on_period_changed(self, _index: int) -> None:
		# Перестраиваем график и сводку сразу при переключении периода
		self.replot()
		index = self.period_tabs.currentIndex()
		days = 1 if index == 0 else (7 if index == 1 else 30)
		self._build_summary_tabs(days)


class MushroomTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state
		self.category = "mushroom"

		self.work_time_label = QLabel("00:00:00")
		self.status_label = QLabel("Статус: Остановлено")
		self.start_button = QPushButton("Start")
		self.stop_button = QPushButton("Stop")
		self.stop_button.setEnabled(False)

		self.items = [
			{"name": "Шампиньон", "market_price": "30-90"},
			{"name": "Вёшенка", "market_price": "35-103"},
			{"name": "Шахматистый", "market_price": "40-107"},
			{"name": "Мухомор", "market_price": "77-235"},
			{"name": "Подболотник", "market_price": "91-224"},
			{"name": "Подберезовик", "market_price": "130-243"},
			{"name": "Золотой гриб", "market_price": "3000-6000"},
		]
		self.qty_inputs: Dict[str, QLineEdit] = {}
		self.price_inputs: Dict[str, QLineEdit] = {}
		self.add_sales_button = QPushButton("Добавить продажи")

		self.session_title_label = QLabel("Сессия: нет")
		self.session_income_label = QLabel("Доход: 0")
		self.session_expense_label = QLabel("Расход: 0")
		self.session_net_label = QLabel("Чистая прибыль: 0")

		self.total_income_label = QLabel("Доход: 0")
		self.total_expense_label = QLabel("Расход: 0")
		self.net_profit_label = QLabel("Чистая прибыль: 0")
		self.rate_hour_label = QLabel("Заработок в час: 0.00")

		self._build_layout()
		self._connect()

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self._tick)
		self._refresh_all()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		time_group = QGroupBox("Рабочее время")
		time_layout = QVBoxLayout()
		time_layout.addWidget(self.work_time_label)
		time_layout.addWidget(self.status_label)
		btns = QHBoxLayout(); btns.addWidget(self.start_button); btns.addWidget(self.stop_button)
		time_layout.addLayout(btns)
		time_group.setLayout(time_layout)

		ore_group = QGroupBox("Сбор грибов за сессию")
		form = QFormLayout()
		for item in self.items:
			row = QHBoxLayout()
			qty = QLineEdit(); qty.setPlaceholderText("шт"); qty.setValidator(QIntValidator(0, 1_000_000_000))
			price = QLineEdit(); price.setPlaceholderText("цена за 1"); price.setValidator(QIntValidator(0, 1_000_000_000))
			market = QLabel(f"Премиум: {item['market_price']} за 1 шт")
			row.addWidget(qty); row.addWidget(price); row.addWidget(market)
			form.addRow(f"{item['name']}:", row)
			self.qty_inputs[item['name']] = qty
			self.price_inputs[item['name']] = price
		row_btn = QHBoxLayout(); row_btn.addStretch(1); row_btn.addWidget(self.add_sales_button)
		form.addRow("", row_btn)
		ore_group.setLayout(form)

		sess_group = QGroupBox("Итого за сессию")
		sess_layout = QVBoxLayout()
		sess_layout.addWidget(self.session_title_label)
		sess_layout.addWidget(self.session_income_label)
		sess_layout.addWidget(self.session_expense_label)
		sess_layout.addWidget(self.session_net_label)
		sess_group.setLayout(sess_layout)

		totals = QGroupBox("Итого за день")
		totals_layout = QVBoxLayout()
		totals_layout.addWidget(self.total_income_label)
		totals_layout.addWidget(self.total_expense_label)
		totals_layout.addWidget(self.net_profit_label)
		totals_layout.addWidget(self.rate_hour_label)
		totals.setLayout(totals_layout)

		root.addWidget(time_group)
		root.addWidget(ore_group)
		root.addWidget(sess_group)
		root.addWidget(totals)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.start_button.clicked.connect(self._on_start)
		self.stop_button.clicked.connect(self._on_stop)
		self.add_sales_button.clicked.connect(self._on_add_sales)

	def _on_start(self) -> None:
		self.state.start(self.category)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.status_label.setText("Статус: Идёт работа")
		self.timer.start()
		self._refresh_totals()

	def _on_stop(self) -> None:
		self.state.stop(self.category)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)
		self.status_label.setText("Статус: Остановлено")
		self.timer.stop()
		self._refresh_totals(); self._refresh_time()

	def _on_add_sales(self) -> None:
		total_income = 0
		for item in self.items:
			qty = parse_amount(self.qty_inputs[item['name']].text()) or 0
			price = parse_amount(self.price_inputs[item['name']].text()) or 0
			if qty > 0 and price >= 0:
				income = qty * price
				total_income += income
				self.state.add_income(income, note=f"Гриб {item['name']} x{qty}", category=self.category)
				self.qty_inputs[item['name']].clear(); self.price_inputs[item['name']].clear()
		if total_income == 0:
			QMessageBox.information(self, "Продажи", "Нет валидных значений для добавления")
		self._refresh_totals()

	def _tick(self) -> None:
		self._refresh_time(); self._refresh_session_totals()

	def _refresh_time(self) -> None:
		self.work_time_label.setText(format_seconds(self.state.total_seconds(self.category)))

	def _refresh_session_totals(self) -> None:
		session = self.state.current_or_last_session(self.category)
		if session is None:
			self.session_title_label.setText("Сессия: нет")
			self.session_income_label.setText("Доход: 0")
			self.session_expense_label.setText("Расход: 0")
			self.session_net_label.setText("Чистая прибыль: 0")
			return
		is_running = session.end_iso is None
		title = "Текущая сессия" if is_running else "Последняя сессия"
		inc, exp, net = self.state.session_totals(session, category=self.category)
		self.session_title_label.setText(title)
		self.session_income_label.setText(f"Доход: {inc:,}".replace(",", " "))
		self.session_expense_label.setText(f"Расход: {exp:,}".replace(",", " "))
		self.session_net_label.setText(f"Чистая прибыль: {net:,}".replace(",", " "))

	def _refresh_totals(self) -> None:
		self.total_income_label.setText(f"Доход: {self.state.total_income(self.category):,}".replace(",", " "))
		self.total_expense_label.setText(f"Расход: {self.state.total_expense(self.category):,}".replace(",", " "))
		self.net_profit_label.setText(f"Чистая прибыль: {self.state.net_profit(self.category):,}".replace(",", " "))
		self.rate_hour_label.setText(f"Заработок в час: {self.state.profit_per_hour(self.category):.2f}")
		self._refresh_session_totals()

	def _refresh_all(self) -> None:
		is_running = self.state.current_session(self.category) is not None
		self.start_button.setEnabled(not is_running)
		self.stop_button.setEnabled(is_running)
		self.status_label.setText("Статус: Идёт работа" if is_running else "Статус: Остановлено")
		if is_running:
			self.timer.start()
		else:
			self.timer.stop()
		self._refresh_totals(); self._refresh_time()


class LoggerTab(QWidget):
	def __init__(self, state: AppState) -> None:
		super().__init__()
		self.state = state
		self.category = "logger"

		self.work_time_label = QLabel("00:00:00")
		self.status_label = QLabel("Статус: Остановлено")
		self.start_button = QPushButton("Start")
		self.stop_button = QPushButton("Stop")
		self.stop_button.setEnabled(False)

		self.items = [
			{"name": "Сосновое бревно", "market_price": "64-107"},
			{"name": "Дубовое бревно", "market_price": "95-158"},
			{"name": "Березовое бревно", "market_price": "125-209"},
			{"name": "Кленовое бревно", "market_price": "156-261"},
			{"name": "Золотая шишка", "market_price": "3000-8000"},
		]
		self.qty_inputs: Dict[str, QLineEdit] = {}
		self.price_inputs: Dict[str, QLineEdit] = {}
		self.add_sales_button = QPushButton("Добавить продажи")

		self.session_title_label = QLabel("Сессия: нет")
		self.session_income_label = QLabel("Доход: 0")
		self.session_expense_label = QLabel("Расход: 0")
		self.session_net_label = QLabel("Чистая прибыль: 0")

		self.total_income_label = QLabel("Доход: 0")
		self.total_expense_label = QLabel("Расход: 0")
		self.net_profit_label = QLabel("Чистая прибыль: 0")
		self.rate_hour_label = QLabel("Заработок в час: 0.00")

		self._build_layout()
		self._connect()

		self.timer = QTimer(self)
		self.timer.setInterval(1000)
		self.timer.timeout.connect(self._tick)
		self._refresh_all()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		time_group = QGroupBox("Рабочее время")
		time_layout = QVBoxLayout()
		time_layout.addWidget(self.work_time_label)
		time_layout.addWidget(self.status_label)
		btns = QHBoxLayout(); btns.addWidget(self.start_button); btns.addWidget(self.stop_button)
		time_layout.addLayout(btns)
		time_group.setLayout(time_layout)

		ore_group = QGroupBox("Сбор древесины за сессию")
		form = QFormLayout()
		for item in self.items:
			row = QHBoxLayout()
			qty = QLineEdit(); qty.setPlaceholderText("шт"); qty.setValidator(QIntValidator(0, 1_000_000_000))
			price = QLineEdit(); price.setPlaceholderText("цена за 1"); price.setValidator(QIntValidator(0, 1_000_000_000))
			market = QLabel(f"Премиум: {item['market_price']} за 1 шт")
			row.addWidget(qty); row.addWidget(price); row.addWidget(market)
			form.addRow(f"{item['name']}:", row)
			self.qty_inputs[item['name']] = qty
			self.price_inputs[item['name']] = price
		row_btn = QHBoxLayout(); row_btn.addStretch(1); row_btn.addWidget(self.add_sales_button)
		form.addRow("", row_btn)
		ore_group.setLayout(form)

		sess_group = QGroupBox("Итого за сессию")
		sess_layout = QVBoxLayout()
		sess_layout.addWidget(self.session_title_label)
		sess_layout.addWidget(self.session_income_label)
		sess_layout.addWidget(self.session_expense_label)
		sess_layout.addWidget(self.session_net_label)
		sess_group.setLayout(sess_layout)

		totals = QGroupBox("Итого за день")
		totals_layout = QVBoxLayout()
		totals_layout.addWidget(self.total_income_label)
		totals_layout.addWidget(self.total_expense_label)
		totals_layout.addWidget(self.net_profit_label)
		totals_layout.addWidget(self.rate_hour_label)
		totals.setLayout(totals_layout)

		root.addWidget(time_group)
		root.addWidget(ore_group)
		root.addWidget(sess_group)
		root.addWidget(totals)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.start_button.clicked.connect(self._on_start)
		self.stop_button.clicked.connect(self._on_stop)
		self.add_sales_button.clicked.connect(self._on_add_sales)

	def _on_start(self) -> None:
		self.state.start(self.category)
		self.start_button.setEnabled(False)
		self.stop_button.setEnabled(True)
		self.status_label.setText("Статус: Идёт работа")
		self.timer.start()
		self._refresh_totals()

	def _on_stop(self) -> None:
		self.state.stop(self.category)
		self.start_button.setEnabled(True)
		self.stop_button.setEnabled(False)
		self.status_label.setText("Статус: Остановлено")
		self.timer.stop()
		self._refresh_totals(); self._refresh_time()

	def _on_add_sales(self) -> None:
		total_income = 0
		for item in self.items:
			qty = parse_amount(self.qty_inputs[item['name']].text()) or 0
			price = parse_amount(self.price_inputs[item['name']].text()) or 0
			if qty > 0 and price >= 0:
				income = qty * price
				total_income += income
				self.state.add_income(income, note=f"Лес {item['name']} x{qty}", category=self.category)
				self.qty_inputs[item['name']].clear(); self.price_inputs[item['name']].clear()
		if total_income == 0:
			QMessageBox.information(self, "Продажи", "Нет валидных значений для добавления")
		self._refresh_totals()

	def _tick(self) -> None:
		self._refresh_time(); self._refresh_session_totals()

	def _refresh_time(self) -> None:
		self.work_time_label.setText(format_seconds(self.state.total_seconds(self.category)))

	def _refresh_session_totals(self) -> None:
		session = self.state.current_or_last_session(self.category)
		if session is None:
			self.session_title_label.setText("Сессия: нет")
			self.session_income_label.setText("Доход: 0")
			self.session_expense_label.setText("Расход: 0")
			self.session_net_label.setText("Чистая прибыль: 0")
			return
		is_running = session.end_iso is None
		title = "Текущая сессия" if is_running else "Последняя сессия"
		inc, exp, net = self.state.session_totals(session, category=self.category)
		self.session_title_label.setText(title)
		self.session_income_label.setText(f"Доход: {inc:,}".replace(",", " "))
		self.session_expense_label.setText(f"Расход: {exp:,}".replace(",", " "))
		self.session_net_label.setText(f"Чистая прибыль: {net:,}".replace(",", " "))

	def _refresh_totals(self) -> None:
		self.total_income_label.setText(f"Доход: {self.state.total_income(self.category):,}".replace(",", " "))
		self.total_expense_label.setText(f"Расход: {self.state.total_expense(self.category):,}".replace(",", " "))
		self.net_profit_label.setText(f"Чистая прибыль: {self.state.net_profit(self.category):,}".replace(",", " "))
		self.rate_hour_label.setText(f"Заработок в час: {self.state.profit_per_hour(self.category):.2f}")
		self._refresh_session_totals()

	def _refresh_all(self) -> None:
		is_running = self.state.current_session(self.category) is not None
		self.start_button.setEnabled(not is_running)
		self.stop_button.setEnabled(is_running)
		self.status_label.setText("Статус: Идёт работа" if is_running else "Статус: Остановлено")
		if is_running:
			self.timer.start()
		else:
			self.timer.stop()
		self._refresh_totals(); self._refresh_time()


class CraftTab(QWidget):
	def __init__(self, base_dir: str) -> None:
		super().__init__()
		self.base_dir = base_dir
		# Инициализация менеджеров крафта и цен
		self.craft_mgr = None
		self.price_mgr = None
		self._ensure_managers()

		# UI элементы
		self.level_input = QLineEdit(); self.level_input.setPlaceholderText("Уровень (1-3)"); self.level_input.setValidator(QIntValidator(1,3))
		self.search_input = QLineEdit(); self.search_input.setPlaceholderText("Поиск рецептов…")
		self.refresh_button = QPushButton("Обновить")
		self.add_button = QPushButton("Добавить рецепт")
		self.edit_button = QPushButton("Изменить")
		self.delete_button = QPushButton("Удалить")
		self.import_button = QPushButton("Импорт CSV")
		self.export_button = QPushButton("Экспорт CSV")
		self.calc_button = QPushButton("Рассчитать прибыль")
		self.prices_button = QPushButton("Изменить цены…")

		self.list_widget = QListWidget()

		self._build_layout()
		self._connect()
		self._reload_list()

	def _ensure_managers(self) -> None:
		try:
			if CraftManager and self.craft_mgr is None:
				recipes_path = os.path.join(self.base_dir, "craft_recipes.csv")
				self.craft_mgr = CraftManager(recipes_path)
		except Exception:
			self.craft_mgr = None
		try:
			if CraftPriceManager and self.price_mgr is None:
				prices_path = os.path.join(self.base_dir, "craft_prices.csv")
				self.price_mgr = CraftPriceManager(prices_file=prices_path)
		except Exception:
			self.price_mgr = None

	def _build_layout(self) -> None:
		root = QVBoxLayout()
		head = QHBoxLayout()
		head.addWidget(QLabel("Уровень:")); head.addWidget(self.level_input)
		head.addWidget(self.search_input)
		head.addWidget(self.refresh_button)
		root.addLayout(head)
		root.addWidget(self.list_widget)
		row = QHBoxLayout()
		row.addWidget(self.add_button); row.addWidget(self.edit_button); row.addWidget(self.delete_button)
		row.addStretch(1)
		row.addWidget(self.import_button); row.addWidget(self.export_button); row.addWidget(self.calc_button); row.addWidget(self.prices_button)
		root.addLayout(row)
		self.setLayout(root)

	def _connect(self) -> None:
		self.refresh_button.clicked.connect(self._reload_list)
		self.search_input.textChanged.connect(self._reload_list)
		self.add_button.clicked.connect(self._on_add)
		self.edit_button.clicked.connect(self._on_edit)
		self.delete_button.clicked.connect(self._on_delete)
		self.import_button.clicked.connect(self._on_import)
		self.export_button.clicked.connect(self._on_export)
		self.calc_button.clicked.connect(self._on_calc)
		self.list_widget.itemDoubleClicked.connect(lambda *_: self._on_edit())
		self.prices_button.clicked.connect(self._on_prices)

	def _reload_list(self) -> None:
		self.list_widget.clear()
		if not self.craft_mgr:
			self.list_widget.addItem("Модуль крафта не найден")
			return
		q = (self.search_input.text() or "").strip()
		try:
			level_text = self.level_input.text().strip()
			if level_text:
				try:
					lvl = int(level_text)
					self.craft_mgr.set_current_level(lvl)
				except Exception:
					pass
			recipes = self.craft_mgr.search_recipes(q) if q else [(n,r,l) for l,d in self.craft_mgr.get_all_recipes().items() for n,r in d.items()]
			for name, rec, lvl in sorted(recipes, key=lambda t: (t[2], t[0])):
				item = QListWidgetItem(f"[{lvl}] {name}")
				item.setData(Qt.UserRole, name)
				self.list_widget.addItem(item)
		except Exception:
			self.list_widget.addItem("Ошибка загрузки рецептов")

	def _prompt_recipe(self, initial: Optional[dict] = None) -> Optional[dict]:
		name, ok = QInputDialog.getText(self, "Название рецепта", "Название:", text=(initial or {}).get("name",""))
		if not ok or not name.strip(): return None
		level_str, ok = QInputDialog.getText(self, "Уровень", "Уровень (1-3):", text=str((initial or {}).get("level",1)))
		if not ok: return None
		try:
			level = max(1, min(3, int(level_str)))
		except Exception:
			level = (initial or {}).get("level", 1)
		materials_str, ok = QInputDialog.getMultiLineText(self, "Материалы", "Материал:количество через запятую",
			", ".join([f"{m}:{q}" for m,q in (initial or {}).get("materials", {}).items()]))
		if not ok: return None
		materials: Dict[str,int] = {}
		for part in [p.strip() for p in materials_str.split(',') if p.strip()]:
			if ':' in part:
				m, q = part.split(':', 1)
				try:
					materials[m.strip()] = int(float(q.strip()))
				except Exception:
					pass
		chance_str, ok = QInputDialog.getText(self, "Шанс", "Шанс успеха (1-100):", text=str((initial or {}).get("success_chance",35)))
		if not ok: return None
		try: chance = max(1, min(100, int(float(chance_str))))
		except Exception: chance = int((initial or {}).get("success_chance",35))
		qty_str, ok = QInputDialog.getText(self, "Количество", "Кол-во за крафт:", text=str((initial or {}).get("quantity",1)))
		if not ok: return None
		try: quantity = max(1, int(float(qty_str)))
		except Exception: quantity = int((initial or {}).get("quantity",1))
		fee_str, ok = QInputDialog.getText(self, "Сбор", "Сбор за попытку:", text=str((initial or {}).get("craft_fee",0)))
		if not ok: return None
		try: craft_fee = max(0, int(float(fee_str)))
		except Exception: craft_fee = int((initial or {}).get("craft_fee",0))
		desc, ok = QInputDialog.getMultiLineText(self, "Описание", "Описание:", (initial or {}).get("description",""))
		if not ok: return None
		return {"name": name.strip(), "level": level, "materials": materials, "success_chance": chance, "quantity": quantity, "craft_fee": craft_fee, "description": desc}

	def _on_add(self) -> None:
		if not self.craft_mgr:
			QMessageBox.warning(self, "Крафт", "Модуль крафта недоступен")
			return
		data = self._prompt_recipe()
		if not data: return
		self.craft_mgr.upsert_recipe(data["level"], data["name"], data["materials"], data["success_chance"], data["quantity"], data["craft_fee"], data["description"])
		self._reload_list()

	def _on_edit(self) -> None:
		if not self.craft_mgr: return
		item = self.list_widget.currentItem()
		if not item:
			QMessageBox.information(self, "Крафт", "Выберите рецепт")
			return
		name = item.data(Qt.UserRole)
		rec = self.craft_mgr.get_recipe(name) or {}
		# Найдем уровень рецепта
		lvl = 1
		for L, d in self.craft_mgr.get_all_recipes().items():
			if name in d: lvl = L; break
		initial = {"name": name, "level": lvl, **rec}
		data = self._prompt_recipe(initial)
		if not data: return
		self.craft_mgr.upsert_recipe(data["level"], data["name"], data["materials"], data["success_chance"], data["quantity"], data["craft_fee"], data["description"])
		self._reload_list()

	def _on_delete(self) -> None:
		if not self.craft_mgr: return
		item = self.list_widget.currentItem()
		if not item: return
		name = item.data(Qt.UserRole)
		# Удаление: перезапишем без этого рецепта
		allr = self.craft_mgr.get_all_recipes()
		for lvl in list(allr.keys()):
			if name in allr[lvl]:
				allr[lvl].pop(name, None)
		# Сохраним вручную
		self.craft_mgr.recipes = allr  # type: ignore
		self.craft_mgr.save_recipes()
		self._reload_list()

	def _on_import(self) -> None:
		if not self.craft_mgr: return
		path, _ = QFileDialog.getOpenFileName(self, "Импорт рецептов", self.base_dir, "CSV (*.csv)")
		if not path: return
		try:
			self.craft_mgr.recipes_file = path  # type: ignore
			self.craft_mgr.load_recipes()
			QMessageBox.information(self, "Импорт", "Рецепты загружены")
		except Exception as e:
			QMessageBox.warning(self, "Импорт", f"Ошибка: {e}")
		self._reload_list()

	def _on_export(self) -> None:
		if not self.craft_mgr: return
		path, _ = QFileDialog.getSaveFileName(self, "Экспорт рецептов", self.base_dir, "CSV (*.csv)")
		if not path: return
		try:
			old = self.craft_mgr.recipes_file  # type: ignore
			self.craft_mgr.recipes_file = path  # type: ignore
			self.craft_mgr.save_recipes()
			self.craft_mgr.recipes_file = old  # type: ignore
			QMessageBox.information(self, "Экспорт", "Сохранено")
		except Exception as e:
			QMessageBox.warning(self, "Экспорт", f"Ошибка: {e}")

	def _on_calc(self) -> None:
		if not (self.craft_mgr and self.price_mgr):
			QMessageBox.warning(self, "Калькулятор", "Нет модулей крафта/цен")
			return
		item = self.list_widget.currentItem()
		if not item:
			QMessageBox.information(self, "Калькулятор", "Выберите рецепт")
			return
		name = item.data(Qt.UserRole)
		# Перед расчётом убедимся, что есть цены на все базовые материалы
		missing = self._find_missing_leaf_prices(name)
		if missing:
			if not self._prompt_set_prices(missing):
				return
			ev = self.craft_mgr.evaluate_profitability(name, self.price_mgr)
		else:
			ev = self.craft_mgr.evaluate_profitability(name, self.price_mgr)
		if not ev:
			QMessageBox.information(self, "Калькулятор", "Невозможно посчитать. Проверьте цены материалов и шанс.")
			return
		info = [
			f"Предмет: {ev['item']}",
			f"Шанс: {ev['chance']:.1f}%",
			f"Кол-во за крафт: {ev.get('quantity',1)}",
			f"Материалы: ${ev['materials_cost']:.0f}",
			f"Сбор: ${ev['fee_per_attempt']:.0f}",
			f"Ожид. себестоимость: ${ev['expected_cost']:.0f}",
			f"Цена продажи: ${ev['sell_price']:.0f}",
			f"Прибыль: ${ev['profit']:.0f}",
		]
		QMessageBox.information(self, "Калькулятор", "\n".join(info))

	def _on_prices(self) -> None:
		"""Открывает диалог правки цен: все материалы рецепта + цена продажи."""
		if not (self.craft_mgr and self.price_mgr):
			QMessageBox.warning(self, "Цены", "Нет модулей крафта/цен")
			return
		item = self.list_widget.currentItem()
		if not item:
			QMessageBox.information(self, "Цены", "Выберите рецепт")
			return
		name = item.data(Qt.UserRole)
		rec = self.craft_mgr.get_recipe(name)
		if not rec:
			QMessageBox.information(self, "Цены", "Рецепт не найден")
			return
		# Составим список позиций: материалы (включая вложенные) и сам предмет (цена продажи)
		positions: Dict[str, Optional[int]] = {}
		def collect(n: str) -> None:
			r = self.craft_mgr.get_recipe(n)
			if r:
				for m,_q in (r.get("materials") or {}).items():
					if self.craft_mgr.get_recipe(m):
						collect(m)
					else:
						positions[m] = self.price_mgr.get_price(m)
			else:
				positions[n] = self.price_mgr.get_price(n)
		collect(name)
		# Добавим сам предмет как продаваемый
		positions[name] = self.price_mgr.get_price(name)
		# Пройдемся по позициям и запросим цену
		for pname, cur in positions.items():
			label = "Цена продажи" if pname == name else "Цена материала"
			val, ok = QInputDialog.getInt(self, label, f"{pname}: текущая цена {cur if cur is not None else '—'}", int(cur or 0), 0, 1_000_000_000, 1)
			if not ok:
				return
			try:
				if hasattr(self.price_mgr, 'set_price'):
					self.price_mgr.set_price(pname, int(val), "Готовый предмет" if pname == name else "Материал")
				else:
					self.price_mgr.prices[pname] = int(val)  # type: ignore[attr-defined]
					self.price_mgr.save_prices()  # type: ignore[attr-defined]
			except Exception:
				QMessageBox.warning(self, "Цены", f"Не удалось сохранить цену: {pname}")
		QMessageBox.information(self, "Цены", "Цены обновлены")

	def _find_missing_leaf_prices(self, item_name: str) -> List[str]:
		"""Собирает список базовых (не имеющих собственного рецепта) материалов без цены."""
		if not (self.craft_mgr and self.price_mgr):
			return []
		seen: set[str] = set()
		missing: set[str] = set()
		def walk(name: str) -> None:
			if name in seen: return
			seen.add(name)
			rec = self.craft_mgr.get_recipe(name)
			if rec:
				for m, _q in (rec.get("materials") or {}).items():
					if self.craft_mgr.get_recipe(m):
						walk(m)
					else:
						if self.price_mgr.get_price(m) is None:
							missing.add(m)
			else:
				# это базовый материал
				if self.price_mgr.get_price(name) is None:
					missing.add(name)
		walk(item_name)
		return sorted(missing)

	def _prompt_set_prices(self, materials: List[str]) -> bool:
		"""Запрашивает у пользователя цены для материалов и сохраняет их. Возвращает True, если всё введено."""
		if not self.price_mgr:
			return False
		for mat in materials:
			price, ok = QInputDialog.getInt(self, "Цена материала", f"{mat}: введите цену за 1 шт", 0, 0, 1_000_000_000, 1)
			if not ok:
				return False
			try:
				# Упростим категорию
				if hasattr(self.price_mgr, 'set_price'):
					self.price_mgr.set_price(mat, int(price), "Материал")
				else:
					# fallback менеджер
					self.price_mgr.prices[mat] = int(price)  # type: ignore[attr-defined]
					self.price_mgr.save_prices()  # type: ignore[attr-defined]
			except Exception:
				return False
		return True

class SettingsManager:
	def __init__(self, base_dir: str) -> None:
		self.base_dir = base_dir
		self.file_path = os.path.join(self.base_dir, "settings.json")

	def load(self) -> Dict[str, Any]:
		default = {
			"opacity": 1.0,
			"always_on_top": False,
			"tabs_visibility": {"stats": True, "trucker": True, "farm": True, "mine": True, "fish": True, "mushroom": True, "logger": True, "craft": True},
			"updates": {"github_manifest_url": DEFAULT_MANIFEST_URL, "auto_check": True},
		}
		try:
			if os.path.exists(self.file_path):
				with open(self.file_path, "r", encoding="utf-8") as f:
					data = json.load(f)
					default.update({k: data.get(k, default[k]) for k in default})
		except Exception:
			pass
		return default

	def save(self, data: Dict[str, Any]) -> None:
		try:
			with open(self.file_path, "w", encoding="utf-8") as f:
				json.dump(data, f, ensure_ascii=False, indent=2)
		except Exception:
			pass


class SettingsTab(QWidget):
	def __init__(self, main_window: QMainWindow, base_dir: str) -> None:
		super().__init__()
		self.main_window = main_window
		self.base_dir = base_dir
		self.data_dir = os.path.join(self.base_dir, "data")
		self.manager = SettingsManager(self.base_dir)
		self.settings = self.manager.load()

		# Controls
		self.opacity_slider = QSlider()
		self.opacity_slider.setOrientation(Qt.Horizontal)
		self.opacity_slider.setMinimum(50)
		self.opacity_slider.setMaximum(100)
		self.opacity_label = QLabel("100%")
		self.always_on_top = QCheckBox("Поверх всех окон")
		self.data_path_label = QLabel(self.data_dir)
		self.data_size_label = QLabel("-")
		self.open_dir_button = QPushButton("Открыть папку")
		self.refresh_size_button = QPushButton("Обновить размер")
		self.update_button = QPushButton("Обновить приложение…")

		# Вкладки: выбор видимости
		self.cb_stats = QCheckBox("Статистика")
		self.cb_trucker = QCheckBox("Дальнобойщик")
		self.cb_farm = QCheckBox("Ферма")
		self.cb_mine = QCheckBox("Карьер")
		self.cb_fish = QCheckBox("Рыбалка")
		self.cb_mushroom = QCheckBox("Грибник")
		self.cb_logger = QCheckBox("Лесоруб")
		self.cb_craft = QCheckBox("Крафт")

		self._build_layout()
		self._connect()
		self._apply_loaded()
		self._update_data_size()

	def _build_layout(self) -> None:
		root = QVBoxLayout()

		win_group = QGroupBox("Окно")
		win_form = QFormLayout()
		op_row = QHBoxLayout()
		op_row.addWidget(self.opacity_slider)
		op_row.addWidget(self.opacity_label)
		win_form.addRow("Прозрачность:", op_row)
		win_form.addRow("Режим:", self.always_on_top)
		win_group.setLayout(win_form)

		data_group = QGroupBox("Данные")
		data_form = QFormLayout()
		btn_row = QHBoxLayout()
		btn_row.addWidget(self.open_dir_button)
		btn_row.addWidget(self.refresh_size_button)
		btn_row.addWidget(self.update_button)
		data_form.addRow("Путь:", self.data_path_label)
		data_form.addRow("Размер:", self.data_size_label)
		data_form.addRow("", btn_row)
		data_group.setLayout(data_form)

		vis_group = QGroupBox("Видимость вкладок")
		vis_form = QFormLayout()
		vis_form.addRow(self.cb_stats)
		vis_form.addRow(self.cb_trucker)
		vis_form.addRow(self.cb_farm)
		vis_form.addRow(self.cb_mine)
		vis_form.addRow(self.cb_fish)
		vis_form.addRow(self.cb_mushroom)
		vis_form.addRow(self.cb_logger)
		vis_form.addRow(self.cb_craft)
		vis_group.setLayout(vis_form)

		root.addWidget(win_group)
		root.addWidget(data_group)
		root.addWidget(vis_group)
		root.addStretch(1)
		self.setLayout(root)

	def _connect(self) -> None:
		self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
		self.always_on_top.toggled.connect(self._on_top_toggled)
		self.open_dir_button.clicked.connect(self._on_open_dir)
		self.refresh_size_button.clicked.connect(self._update_data_size)
		self.update_button.clicked.connect(self._on_update)
		self.cb_stats.toggled.connect(lambda v: self._on_tab_toggle('stats', v))
		self.cb_trucker.toggled.connect(lambda v: self._on_tab_toggle('trucker', v))
		self.cb_farm.toggled.connect(lambda v: self._on_tab_toggle('farm', v))
		self.cb_mine.toggled.connect(lambda v: self._on_tab_toggle('mine', v))
		self.cb_fish.toggled.connect(lambda v: self._on_tab_toggle('fish', v))
		self.cb_mushroom.toggled.connect(lambda v: self._on_tab_toggle('mushroom', v))
		self.cb_logger.toggled.connect(lambda v: self._on_tab_toggle('logger', v))
		self.cb_craft.toggled.connect(lambda v: self._on_tab_toggle('craft', v))

	def _apply_loaded(self) -> None:
		opacity = float(self.settings.get("opacity", 1.0))
		self.main_window.setWindowOpacity(opacity)
		self.opacity_slider.setValue(int(round(opacity * 100)))
		self.opacity_label.setText(f"{int(round(opacity * 100))}%")
		if self.settings.get("always_on_top", False):
			from PySide6.QtCore import Qt
			self.main_window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
			self.main_window.show()
		self.always_on_top.setChecked(bool(self.settings.get("always_on_top", False)))
		# Применим видимость вкладок
		vis = dict(self.settings.get('tabs_visibility', {}))
		self.cb_stats.setChecked(bool(vis.get('stats', True)))
		self.cb_trucker.setChecked(bool(vis.get('trucker', True)))
		self.cb_farm.setChecked(bool(vis.get('farm', True)))
		self.cb_mine.setChecked(bool(vis.get('mine', True)))
		self.cb_fish.setChecked(bool(vis.get('fish', True)))
		self.cb_mushroom.setChecked(bool(vis.get('mushroom', True)))
		self.cb_logger.setChecked(bool(vis.get('logger', True)))
		self.cb_craft.setChecked(bool(vis.get('craft', True)))
		self._apply_tabs_visibility()

	def _on_opacity_changed(self, value: int) -> None:
		opacity = max(0.5, min(1.0, value / 100.0))
		self.opacity_label.setText(f"{int(round(opacity * 100))}%")
		self.main_window.setWindowOpacity(opacity)
		self.settings["opacity"] = opacity
		self.manager.save(self.settings)

	def _on_top_toggled(self, checked: bool) -> None:
		from PySide6.QtCore import Qt
		self.main_window.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
		self.main_window.show()
		self.settings["always_on_top"] = bool(checked)
		self.manager.save(self.settings)

	def _on_open_dir(self) -> None:
		try:
			os.startfile(self.data_dir)
		except Exception:
			QMessageBox.information(self, "Папка", f"Открой вручную: {self.data_dir}")

	def _on_update(self) -> None:
		# Показываем диалог сравнения версий и предлагаем только релизные обновления
		self.main_window.show_update_prompt()

	def _update_data_size(self) -> None:
		total = 0
		for root, _, files in os.walk(self.data_dir):
			for name in files:
				if name.endswith('.json'):
					total += os.path.getsize(os.path.join(root, name))
		self.data_size_label.setText(self._format_bytes(total))

	def _on_tab_toggle(self, key: str, checked: bool) -> None:
		vis = dict(self.settings.get('tabs_visibility', {}))
		vis[key] = bool(checked)
		self.settings['tabs_visibility'] = vis
		self.manager.save(self.settings)
		self._apply_tabs_visibility()

	def _apply_tabs_visibility(self) -> None:
		# Попросим главное окно пересобрать вкладки согласно настройке
		try:
			self.main_window.apply_tabs_visibility(self.settings.get('tabs_visibility', {}))
		except Exception:
			pass

	@staticmethod
	def _format_bytes(num: int) -> str:
		for unit in ["Б", "КБ", "МБ", "ГБ", "ТБ"]:
			if num < 1024:
				return f"{num:.1f} {unit}"
			num /= 1024
		return f"{num:.1f} ПБ"


class MainWindow(QMainWindow):
	def __init__(self) -> None:
		super().__init__()
		self.setWindowTitle("MajesticRP Статистика")
		self.resize(900, 600)

		self.storage = DayStorage(base_dir=self._data_dir())
		self.state = AppState(storage=self.storage)

		# Проверку обновлений покажем позже, чтобы не задерживать запуск UI

		self.tabs = QTabWidget()
		self.stats_tab = StatsTab(self.state)
		self.trucker_tab = TruckerTab(self.state)
		self.farm_tab = FarmTab(self.state)
		self.mine_tab = MineTab(self.state)
		self.fish_tab = FishTab(self.state)
		self.mushroom_tab = MushroomTab(self.state)
		self.logger_tab = LoggerTab(self.state)
		# Вкладка Крафт (после Лесоруба)
		base_dir = os.path.dirname(self.storage.data_dir)
		self.craft_tab = CraftTab(base_dir)
		self._tab_order = [
			('stats', self.stats_tab, 'Статистика'),
			('trucker', self.trucker_tab, 'Дальнобойщик'),
			('farm', self.farm_tab, 'Ферма'),
			('mine', self.mine_tab, 'Карьер'),
			('fish', self.fish_tab, 'Рыбалка'),
			('mushroom', self.mushroom_tab, 'Грибник'),
			('logger', self.logger_tab, 'Лесоруб'),
			('craft', self.craft_tab, 'Крафт'),
		]
		self.apply_tabs_visibility(self._load_tabs_visibility())
		base_dir = os.path.dirname(self.storage.data_dir)
		self.settings_tab = SettingsTab(self, base_dir)
		self.tabs.addTab(self.settings_tab, "Настройки")

		# Верхняя панель с версией справа
		try:
			top_bar = QWidget(); top_h = QHBoxLayout(top_bar); top_h.setContentsMargins(8, 6, 8, 6)
			top_h.addStretch(1)
			self.version_label = QLabel("")
			fnt = self.version_label.font(); fnt.setPointSizeF(max(9.0, fnt.pointSizeF()-0.5)); self.version_label.setFont(fnt)
			self.version_label.setToolTip("Версия приложения")
			self.version_label.setText(self._format_version_label())
			# Кнопка Discord слева от версии
			self.discord_button = QPushButton("Discord")
			self.discord_button.setToolTip("Открыть Discord сообщество")
			self.discord_button.clicked.connect(lambda: webbrowser.open('https://discord.gg/n5hcWe2JUg'))
			top_h.addWidget(self.discord_button)
			top_h.addWidget(self.version_label)

			wrapper = QWidget(); v = QVBoxLayout(wrapper); v.setContentsMargins(0,0,0,0); v.setSpacing(0)
			v.addWidget(top_bar)
			v.addWidget(self.tabs)
			self.setCentralWidget(wrapper)
		except Exception:
			# Фолбэк: без верхней панели
			self.setCentralWidget(self.tabs)
	def _load_tabs_visibility(self) -> Dict[str, bool]:
		try:
			mgr = SettingsManager(os.path.dirname(self.storage.data_dir))
			settings = mgr.load()
			vis = settings.get('tabs_visibility', {})
			return {k: bool(vis.get(k, True)) for k, _tab, _t in self._tab_order}
		except Exception:
			return {k: True for k, _tab, _t in self._tab_order}

	def apply_tabs_visibility(self, vis: Dict[str, bool]) -> None:
		# Очистим и добавим заново согласно порядку и флагам
		self.tabs.clear()
		for key, tab, title in self._tab_order:
			if bool(vis.get(key, True)):
				self.tabs.addTab(tab, title)

		# Установка иконки после построения UI и инициализации путей
		try:
			app_dir = self._app_dir()
			icon_path = os.path.join(app_dir, "icon.ico")
			if os.path.exists(icon_path):
				from PySide6.QtGui import QIcon
				self.setWindowIcon(QIcon(icon_path))
		except Exception:
			pass

		# Отложенная проверка обновлений после старта UI (не блокирует запуск)
		try:
			QTimer.singleShot(3000, self._check_version_on_startup)
		except Exception:
			pass

	def closeEvent(self, event) -> None:  # type: ignore[override]
		# Остановим все активные сессии
		self.state.stop("trucker")
		self.state.stop("farm")
		self.state.stop("mine")
		self.state.stop("fish")
		self.state.stop("mushroom")
		self.state.stop("logger")
		event.accept()

	@staticmethod
	def _app_dir() -> str:
		if getattr(sys, 'frozen', False):
			# PyInstaller onefile: используем временную папку где распакованы файлы
			if hasattr(sys, '_MEIPASS'):
				return sys._MEIPASS
			# PyInstaller onedir: используем папку с exe
			return os.path.dirname(sys.executable)
		return os.path.dirname(os.path.abspath(__file__))

	@staticmethod
	def _data_dir() -> str:
		base = os.getenv('APPDATA') or os.path.expanduser('~')
		# Новое имя приложения: GrimmStats. Переносим данные из старой папки при первом запуске
		new_path = os.path.join(base, 'GrimmStats')
		old_path = os.path.join(base, 'MajesticRPStats')
		try:
			if os.path.isdir(old_path) and not os.path.isdir(new_path):
				os.rename(old_path, new_path)
		except Exception:
			pass
		path = new_path
		os.makedirs(path, exist_ok=True)
		return path

	@staticmethod
	def _log(msg: str) -> None:
		try:
			log_path = os.path.join(MainWindow._data_dir(), 'updater.log')
			with open(log_path, 'a', encoding='utf-8') as f:
				f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
		except Exception:
			pass

	def _check_version_on_startup(self) -> None:
		"""Фоновая проверка версии после старта: только показывает предложение обновиться."""
		try:
			local_version = self._get_local_version()
			self._log(f"local_version={local_version}")
			manifest = self._fetch_manifest()
			if not manifest:
				self._log("manifest: None (fetch failed)")
				return
			remote_version = int(manifest.get('version', 0))
			self._log(f"remote_version={remote_version}")
			if remote_version > local_version:
				# Не обновляем автоматически. Покажем диалог для пользователя.
				self.show_update_prompt()
		except Exception as e:
			self._log(f"_check_version_on_startup error: {e}")

	def _get_local_version(self) -> int:
		"""Получает локальную версию из version.json рядом с exe."""
		try:
			app_dir = self._app_dir()
			version_file = os.path.join(app_dir, "version.json")
			if os.path.exists(version_file):
				with open(version_file, 'r', encoding='utf-8-sig') as f:
					data = json.load(f)
					return int(data.get('version', 0))
		except Exception:
			pass
		return 0

	def _get_local_semver(self) -> str:
		"""Возвращает локальный semver (X.Y.Z), если доступен, иначе рассчитывает из числа."""
		try:
			app_dir = self._app_dir()
			version_file = os.path.join(app_dir, 'version.json')
			if os.path.exists(version_file):
				with open(version_file, 'r', encoding='utf-8-sig') as f:
					data = json.load(f)
					sem = str(data.get('semver') or '')
					if sem:
						return sem
					num = int(data.get('version', 0))
					if num:
						major = num // 100; minor = (num % 100) // 10; patch = num % 10
						return f"{major}.{minor}.{patch}"
		except Exception:
			pass
		num = self._get_local_version()
		major = num // 100; minor = (num % 100) // 10; patch = num % 10
		return f"{major}.{minor}.{patch}"

	def _format_version_label(self) -> str:
		try:
			return f"v{self._get_local_semver()}"
		except Exception:
			return "v0.0.0"

	def _fetch_manifest(self) -> Optional[dict]:
		"""Возвращает содержимое манифеста (dict) только из GitHub (Raw/настроенный URL)."""
		try:
			mgr = SettingsManager(os.path.dirname(self.storage.data_dir))
			st = mgr.load()
			u = (st.get('updates', {}) or {}).get('github_manifest_url') or DEFAULT_MANIFEST_URL
			cj = _cookiejar.CookieJar()
			opener = _urlrequest.build_opener(_urlrequest.HTTPCookieProcessor(cj))
			opener.addheaders = [('User-Agent','Mozilla/5.0')]
			with opener.open(u, timeout=20) as resp:
				content = resp.read().decode('utf-8-sig', errors='ignore')
				obj = json.loads(content)
				self._log(f"manifest loaded from {u}")
				return obj
		except Exception as e:
			self._log(f"_fetch_manifest error: {e}")
			return None

	def _auto_update_to_version(self, new_version: int, exe_file_id: Optional[str]) -> None:
		"""Автоматически скачивает и запускает updater для новой версии (только GitHub)."""
		try:
			# URL для скачивания берём только из GitHub манифеста (exe_url)
			app_dir = self._app_dir()
			manifest = self._fetch_manifest() or {}
			exe_url = manifest.get('exe_url')
			if not exe_url:
				raise RuntimeError('В манифесте отсутствует exe_url')
			# Создаем временный файл для скачивания
			temp_dir = tempfile.gettempdir()
			temp_exe = os.path.join(temp_dir, f"GrimmStats_v{new_version}.exe")
			# Скачиваем надёжным методом с обработкой confirm
			self._http_download(exe_url, temp_exe)
		# Запускаем updater, текущее приложение корректно закроется
			self._log(f"downloaded new exe to {temp_exe}")
			self._run_updater_or_launch(temp_exe)
		except Exception as e:
			self._log(f"_auto_update_to_version error: {e}")

	def show_update_prompt(self) -> None:
		"""Показывает диалог с текущей и последней версиями. Предлагает обновление только если последняя не предрелизная и новее локальной."""
		def run():
			try:
				local_num = self._get_local_version()
				local_sem = self._get_local_semver()
				manifest = self._fetch_manifest() or {}
				remote_num = int(manifest.get('version', 0))
				remote_sem = str(manifest.get('semver') or '')
				is_prerelease = ('-' in remote_sem)
				if not manifest or not remote_sem:
					QMessageBox.information(self, "Обновление", "Не удалось получить информацию о версии с GitHub")
					return
				if is_prerelease:
					QMessageBox.information(self, "Обновление", f"Текущая: v{local_sem}\nНовая доступная: v{remote_sem} (предрелиз)\n\nПредлагаем только релизные версии. Подождите стабильный релиз.")
					return
				if remote_num <= local_num:
					QMessageBox.information(self, "Обновление", f"У вас актуальная версия: v{local_sem}")
					return
				# Предложить релизное обновление
				ret = QMessageBox.question(self, "Обновление", f"У вас: v{local_sem}\nДоступна новая релизная: v{remote_sem}.\nСкачать и установить?")
				if ret == QMessageBox.StandardButton.Yes:
					self._auto_update_to_version(remote_num, None)
			except Exception as e:
				QMessageBox.warning(self, "Обновление", f"Ошибка проверки: {e}")
		QTimer.singleShot(0, run)

	def _download_file(self, url: str, dest_path: str) -> bool:
		"""Скачивает файл по URL в указанное место."""
		try:
			req = _urlrequest.Request(url)
			with _urlrequest.urlopen(req, timeout=30) as response:
				if response.status == 200:
					with open(dest_path, 'wb') as f:
						shutil.copyfileobj(response, f)
					return True
		except Exception as e:
			print(f"Ошибка скачивания: {e}")
		return False
	def _check_updates_background(self) -> None:
		"""Проверяет новую версию по GitHub и предлагает скачать и установить."""
		try:
			manifest = self._fetch_manifest()
			if not manifest:
				return
			file_name = 'GrimmStats.exe'
			dl_url = manifest.get('exe_url')
			if not dl_url:
				return
			self._ask_download_update(file_name, dl_url)
		except Exception:
			pass

	def force_check_updates(self, file_id_override: str = "") -> None:
		def run():
			try:
				manifest = self._fetch_manifest()
				if not manifest:
					QMessageBox.information(self, "Обновление", "Не удалось получить манифест с GitHub")
					return
				file_name = 'GrimmStats.exe'
				dl_url = manifest.get('exe_url')
				if not dl_url:
					QMessageBox.information(self, "Обновление", "В манифесте отсутствует exe_url")
					return
				self._ask_download_update(file_name, dl_url)
			except Exception as e:
				QMessageBox.warning(self, "Обновление", f"Ошибка проверки: {e}")
		QTimer.singleShot(0, run)

	def update_from_local_or_drive(self, file_id_override: str = "") -> None:
		"""Обновить до последней версии: загрузка только с GitHub по манифесту."""
		def run():
			try:
				manifest = self._fetch_manifest()
				if not manifest:
					QMessageBox.information(self, "Обновление", "Не удалось получить манифест с GitHub")
					return
				file_name = 'GrimmStats.exe'
				dl_url = manifest.get('exe_url')
				if not dl_url:
					QMessageBox.information(self, "Обновление", "В манифесте отсутствует exe_url")
					return
				self._ask_download_update(file_name, dl_url)
			except Exception as e:
				QMessageBox.warning(self, "Обновление", f"Ошибка обновления: {e}")
		QTimer.singleShot(0, run)

	# Удалены все функции и ссылки, связанные с Google Drive

	def _ask_download_update(self, file_name: str, dl_url: str) -> None:
		def _prompt():
			ret = QMessageBox.question(self, "Обновление", f"Найдена новая версия: {file_name}.\nСкачать сейчас?")
			if ret != QMessageBox.StandardButton.Yes:
				return
			# Скачиваем во временный файл
			try:
				tmp_fd, tmp_path = tempfile.mkstemp(prefix="GrimmStats_", suffix=".exe")
				os.close(tmp_fd)
				self._http_download(dl_url, tmp_path)
				# Запускаем updater.exe для подмены
				self._run_updater_or_launch(tmp_path)
			except Exception as e:
				QMessageBox.warning(self, "Обновление", f"Не удалось скачать обновление: {e}")
		# Показать в GUI-потоке
		QTimer.singleShot(0, _prompt)

	@staticmethod
	def _http_get(url: str) -> str:
		try:
			cj = _cookiejar.CookieJar()
			opener = _urlrequest.build_opener(_urlrequest.HTTPCookieProcessor(cj))
			with opener.open(url, timeout=10) as resp:
				return resp.read().decode('utf-8', errors='ignore')
		except Exception:
			return ""

	@staticmethod
	def _http_head_last_modified(url: str) -> Optional[int]:
		try:
			# 1) Пробуем HEAD
			req = _urlrequest.Request(url, method='HEAD', headers={'User-Agent':'Mozilla/5.0'})
			try:
				with _urlrequest.urlopen(req, timeout=10) as resp:
					lm = resp.headers.get('Last-Modified')
					if lm:
						parsed = _email_utils.parsedate_to_datetime(lm)
						return int(parsed.timestamp())
			except Exception:
				pass
			# 2) Fallback: GET с Range, читаем только 1 байт, достаём заголовки
			req2 = _urlrequest.Request(url, headers={'Range':'bytes=0-0','User-Agent':'Mozilla/5.0'})
			with _urlrequest.urlopen(req2, timeout=15) as resp2:
				lm = resp2.headers.get('Last-Modified')
				if lm:
					parsed = _email_utils.parsedate_to_datetime(lm)
					return int(parsed.timestamp())
			return None
		except Exception:
			return None

	@staticmethod
	def _http_download(url: str, dst_path: str) -> None:
		# Надёжное скачивание общедоступного файла Google Drive
		cj = _cookiejar.CookieJar()
		opener = _urlrequest.build_opener(_urlrequest.HTTPCookieProcessor(cj))
		opener.addheaders = [('User-Agent','Mozilla/5.0')]
		data = b''
		
		# 0) Если это ссылка с id=, сначала пробуем стабильный зеркальный хост (обычно без confirm)
		qid = ''
		if 'id=' in url:
			try:
				from urllib.parse import urlparse, parse_qs
				qid = parse_qs(urlparse(url).query).get('id', [''])[0]
				if qid:
					alt = f'https://drive.usercontent.google.com/download?id={qid}&export=download'
					try:
						resp = opener.open(alt, timeout=30)
						data = resp.read()
					except Exception:
						data = b''
			except Exception:
				qid = ''

		# 1) Обычная ссылка uc?export=download — при необходимости обрабатываем confirm
		if not data:
			try:
				resp = opener.open(url, timeout=30)
				data = resp.read()
				# Если HTML — ищем ссылку confirm
				if data.startswith(b'<!') or b'<html' in data[:500].lower():
					text = data.decode('utf-8', errors='ignore')
					m = re.search(r'href=\"(/uc\?export=download[^\"]*confirm=[^\"]+)', text)
					if m:
						confirm_url = 'https://drive.google.com' + m.group(1).replace('&amp;', '&')
						resp = opener.open(confirm_url, timeout=30)
						data = resp.read()
					elif qid:
						# Фолбэк: на зеркальный хост, если confirm не найден
						alt = f'https://drive.usercontent.google.com/download?id={qid}&export=download'
						resp = opener.open(alt, timeout=30)
						data = resp.read()
			except Exception:
				data = b''

		if not data:
			raise RuntimeError('Пустой ответ при скачивании файла')
		with open(dst_path, 'wb') as f:
			f.write(data)

	def _updater_path(self) -> Optional[str]:
		"""Возвращает путь к updater.exe. Если он встроен в onefile, копирует его в папку данных.
		Порядок поиска:
		1) %APPDATA%\\GrimmStats\\updater.exe (persist)
		2) Временная папка PyInstaller (_MEIPASS) -> копируем в (1)
		3) Папка рядом с exe (onedir/ручная поставка)
		"""
		try:
			persist = os.path.join(MainWindow._data_dir(), 'updater.exe')
			if os.path.exists(persist):
				return persist
			# Источник 1: _MEIPASS (встроенный бинарь)
			source_candidates: list[str] = []
			if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
				source_candidates.append(os.path.join(sys._MEIPASS, 'updater.exe'))  # type: ignore[attr-defined]
			# Источник 2: рядом с exe
			base = os.path.dirname(sys.executable) if getattr(sys,'frozen',False) else os.path.dirname(os.path.abspath(__file__))
			source_candidates.append(os.path.join(base, 'updater.exe'))
			for src in source_candidates:
				try:
					if os.path.exists(src):
						# Копируем во внешнюю постоянную папку
						shutil.copy2(src, persist)
						return persist
				except Exception:
					pass
			return None
		except Exception:
			return None

	def _run_updater_or_launch(self, source_exe: str) -> None:
		# Перед обновлением принудительно сохраним данные/настройки
		try:
			self.state._autosave()
			mgr = SettingsManager(os.path.dirname(self.storage.data_dir))
			cur = mgr.load(); mgr.save(cur)
		except Exception:
			pass

		updater = self._updater_path()
		app_path = sys.executable if getattr(sys,'frozen',False) else None
		if updater and app_path:
			try:
				import subprocess, os
				DETACHED_PROCESS = 0x00000008
				CREATE_NEW_PROCESS_GROUP = 0x00000200
				subprocess.Popen(
					[updater, '--app-path', app_path, '--source-exe', source_exe],
					close_fds=True,
					creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
				)
				app = QApplication.instance()
				if app:
					app.quit()
				os._exit(0)
				return
			except Exception:
				pass
		# Фолбэк: запускаем новый exe напрямую
		try:
			import os
			os.startfile(source_exe)
			app = QApplication.instance()
			if app:
				app.quit()
			os._exit(0)
		except Exception:
			QMessageBox.information(self, "Обновление", f"Скачано: {source_exe}\nЗапусти новый файл вручную.")

def main() -> None:
	app = QApplication(sys.argv)
	window = MainWindow()
	window.show()
	sys.exit(app.exec())


if __name__ == "__main__":
	main()
