# -*- coding: utf-8 -*-
# Данный скрипт создаёт отчёт о количестве детектированных людей с разбивкой по зонами детекции
# Дневной отчет формируется в 00:00
# Недельный и месячный отчеты формируются по окончанию недели
# и месяца соответственно
# Отчет по расписанию включает в себя информацию за предыдущие
# сутки, плюс за текущий день
# Имена зон для детектирования событий общие
# Избегайте пересечения выбранных зон, иначе объект будет посчитан несколько раз
# Скрипт запускается на клиенте
# Для работы скрипта необходимо создать БД PostgresSQL*
# * Эта база должна быть независимой от основной БД, используемой Trassir

'''
<parameters>
	<company>MEV</company>
	<title>People count reporter</title>
	<version>2.2</version>
<parameter>
 <type>caption</type>
 <name>Настройки каналов и зон</name>
</parameter>
<parameter>
    <type>objects</type>
    <id>CHANNELS</id>
    <name>Выбрать каналы</name>
    <value></value>
</parameter>
<parameter>
    <type>integer</type>
    <name>Уверенность для детектирования (1-100)</name>
    <id>CONFIDENCE</id>
    <value>95</value>
    <min>1</min>
    <max>100</max>
</parameter>
<parameter>
    <type>objects</type>
    <id>ZONES</id>
    <name>Имена зон для детектирования событий (подсчет ведется если детекция в этой зоне)</name>
    <value></value>
</parameter>
 <parameter>
       <type>caption</type>
       <name>Настройка e-mail</name>
 </parameter>
 <parameter>
        <name>Имя учётной записи e-mail в Trassir</name>
        <id>MAIL_ACCOUNT</id>
        <value></value>
        <type>string</type>
    </parameter>
    <parameter>
        <name>Тема письма</name>
        <id>MAIL_SUBJECT</id>
        <value>Отчёт о количестве детектированных людей</value>
        <type>string</type>
    </parameter>
    <parameter>
        <name>Send reports to emails</name>
        <id>MAIL_RECIPIENTS</id>
        <value></value>
        <type>string</type>
    </parameter>
    <parameter>
        <type>caption</type>
        <name>Настройки БД</name>
    </parameter>
    <parameter>
    <type>string</type>
    <name>Имя базы данных</name>
    <id>DATA_BASE_NAME</id>
    <value>people_quantity_reporter</value>
    </parameter>
    <parameter>
    <type>string</type>
     <name>Логин базы данных</name>
    <id>DATA_BASE_LOGIN</id>
    <value>postgres</value>
    </parameter>
    <parameter>
    <type>string</type>
    <name>Пароль базы данных</name>
    <id>DATA_BASE_PASS</id>
    <value>123456</value>
    </parameter>
     <parameter>
        <type>caption</type>
        <name>Отчет по расписанию</name>
    </parameter>
     <parameter>
        <type>boolean</type>
        <name>Отправлять отчет за два дня по расписанию</name>
        <id>ENABLE_SCHEDULE</id>
        <value>True</value>
    </parameter>
    <parameter>
		<id>SCHEDULE</id>
		<name>Расписание для работы тревоги</name>
		<type>objects</type>
	</parameter>
	<parameter>
		<id>SCHEDULE_COLOR</id>
		<name>Цвет расписания для работы тревоги</name>
		<type>string_from_list</type>
		<value>Красный</value>
		<string_list>Красный,Зелёный,Синий</string_list>
	</parameter>

    <parameter>
        <type>caption</type>
        <name>Дополнительные настройки</name>
    </parameter>

<parameter>
        <type>boolean</type>
        <name>Сделать отчет при запуске скрипта</name>
        <id>EXPORT_AT_STARTUP</id>
        <value>True</value>
</parameter>
 <parameter>
        <type>boolean</type>
        <name>Вести лог</name>
        <id>DEBUG</id>
        <value>True</value>
    </parameter>

</parameters>
'''

import re
import os
import ssl
import time
import host
import json
import pickle
import base64
import ftplib
import urllib
import urllib2
import httplib
import logging
import threading
import subprocess

from Queue import Queue
from functools import wraps
from collections import deque
from xml.etree import ElementTree
from datetime import datetime, date, timedelta
from __builtin__ import object as py_object

import calendar
from sqlalchemy import MetaData
from sqlalchemy import Table, Column, Integer, BigInteger, Unicode, create_engine, select, and_
import psycopg2
import xlsxwriter


class ScriptError(Exception):
    """Base script exception"""

    pass


class HostLogHandler(logging.Handler):
    """ Trassir main log handler """

    def __init__(self, host_api=host):
        super(HostLogHandler, self).__init__()
        self._host_api = host_api

    def emit(self, record):
        msg = self.format(record)
        self._host_api.log_message(msg)


class PopupHandler(logging.Handler):
    """ Trassir popup handler """

    def __init__(self, host_api=host):
        super(PopupHandler, self).__init__()
        self._host_api = host_api
        self._popups = {
            "CRITICAL": host_api.error,
            "FATAL": host_api.error,
            "ERROR": host_api.error,
            "WARN": host_api.alert,
            "WARNING": host_api.alert,
            "INFO": host_api.message,
            "DEBUG": host_api.message,
            "NOTSET": host_api.message,
        }

    def emit(self, record):
        msg = self.format(record)
        popup = self._popups.get(record.levelname, self._host_api.message)
        popup(msg)


class MyFileHandler(logging.Handler):
    """ My file handler """

    def __init__(self, file_path, max_mbytes=10240):
        super(MyFileHandler, self).__init__()
        self._file_path = file_path
        self._max_mbytes = max_mbytes * 1024

    def _check_size(self):
        if not os.path.isfile(self._file_path):
            return
        if os.stat(self._file_path).st_size > self._max_mbytes:
            old_file_path = self._file_path + ".old"
            if os.path.isfile(old_file_path):
                os.remove(old_file_path)
            os.rename(self._file_path, old_file_path)

    def emit(self, record):
        self._check_size()
        msg = self.format(record)
        with open(self._file_path, "a") as log_file:
            log_file.write(msg + "\n")


class BaseUtils:
    """Base utils for your scripts"""

    _host_api = host
    _FOLDERS = {obj[1]: obj[3] for obj in host.objects_list("Folder")}
    _TEXT_FILE_EXTENSIONS = [".txt", ".csv", ".log"]
    _LPR_FLAG_BITS = {
        "LPR_UP": 0x00001,
        "LPR_DOWN": 0x00002,
        "LPR_BLACKLIST": 0x00004,
        "LPR_WHITELIST": 0x00008,
        "LPR_INFO": 0x00010,
        "LPR_FIRST_LANE": 0x01000,
        "LPR_SECOND_LANE": 0x02000,
        "LPR_THIRD_LANE": 0x04000,
        "LPR_EXT_DB_ERROR": 0x00020,
        "LPR_CORRECTED": 0x00040,
    }
    _IMAGE_EXT = [".png", ".jpg", ".jpeg", ".bmp"]
    _HTML_IMG_TEMPLATE = """<img src="data:image/png;base64,{img}" {attr}>"""

    _SCR_DEFAULT_NAMES = [
        "Yeni skript",
        "Unnamed Script",
        "უსახელო სკრიპტი",
        "Жаңа скрипт",
        "Script nou",
        "Новый скрипт",
        "Yeni skript dosyası",
        "Новий скрипт",
        "未命名脚本",
    ]

    def __init__(self):
        pass

    @staticmethod
    def do_nothing(*args, **kwargs):
        """Ничего не делает.

        Returns:
            :obj:`bool`: ``True``
        """
        return True

    @classmethod
    def run_as_thread_v2(cls, locked=False, daemon=True):
        """Декоратор для запуска функций в отдельном потоке.

        Args:
            locked (:obj:`bool`, optional): Если :obj:`True` - запускает поток с блокировкой
                доступа к ресурсам. По умолчанию :obj:`False`
            daemon (:obj:`bool`, optional): Устанавливает значение :obj:`threading.Thread.daemon`.
                По умолчанию :obj:`True`

        Examples:
            >>> import time
            >>>
            >>>
            >>> @BaseUtils.run_as_thread_v2()
            >>> def run_count_timer():
            >>>     time.sleep(1)
            >>>     host.stats()["run_count"] += 1
            >>>
            >>>
            >>> run_count_timer()
        """
        lock = threading.Lock()

        def wrapped(fn):
            @wraps(fn)
            def run(*args, **kwargs):
                def raise_exc(err):
                    args = list(err.args)
                    args[0] = "[{}]: {}".format(fn.__name__, args[0])
                    err.args = args
                    raise err

                def locked_fn(*args_, **kwargs_):
                    lock.acquire()
                    try:
                        return fn(*args_, **kwargs_)
                    except Exception as err:
                        cls._host_api.timeout(1, lambda: raise_exc(err))
                    finally:
                        lock.release()

                def unlocked_fn(*args_, **kwargs_):
                    try:
                        return fn(*args_, **kwargs_)
                    except Exception as err:
                        cls._host_api.timeout(1, lambda: raise_exc(err))

                t = threading.Thread(
                    target=locked_fn if locked else unlocked_fn,
                    args=args,
                    kwargs=kwargs,
                )
                t.daemon = daemon
                t.start()
                return t

            return run

        return wrapped

    @staticmethod
    def run_as_thread(fn):
        """Декоратор для запуска функций в отдельном потоке.

        Returns:
            :obj:`threading.Thread`: Функция в отдельном потоке

        Examples:
            >>> import time
            >>>
            >>>
            >>> @BaseUtils.run_as_thread
            >>> def run_count_timer():
            >>>     time.sleep(1)
            >>>     host.stats()["run_count"] += 1
            >>>
            >>>
            >>> run_count_timer()
        """

        @wraps(fn)
        def run(*args, **kwargs):
            t = threading.Thread(target=fn, args=args, kwargs=kwargs)
            t.daemon = True
            t.start()
            return t

        return run

    @staticmethod
    def catch_request_exceptions(func):
        """Catch request errors"""

        @wraps(func)
        def wrapped(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except urllib2.HTTPError as e:
                return e.code, "HTTPError: {}".format(e.code)
            except urllib2.URLError as e:
                return e.reason, "URLError: {}".format(e.reason)
            except httplib.HTTPException as e:
                return e, "HTTPException: {}".format(e)
            except ssl.SSLError as e:
                return e.errno, "SSLError: {}".format(e)

        return wrapped

    @staticmethod
    def win_encode_path(path):
        """Изменяет кодировку на ``"cp1251"`` для WinOS.

        Args:
            path (:obj:`str`): Путь до файла или папки

        Returns:
            :obj:`str`: Декодированый путь до файла или папки

        Examples:
            >>> path = r"D:\Shots\Скриншот.jpeg"
            >>> os.path.isfile(path)
            False
            >>> os.path.isfile(BaseUtils.win_encode_path(path))
            True
        """
        if os.name == "nt":
            path = path.decode("utf8").encode("cp1251")

        return path

    @staticmethod
    def is_file_exists(file_path, tries=1):
        """Проверяет, существует ли файл.

        Проверка происходит в течении ``tries`` секунд.

        Warning:
            | Запускайте функцию только в отдельном потоке если ``tries > 1``
            | Вторая и последующие проверки производятся с ``time.sleep(1)``

        Args:
            file_path (:obj:`str`): Полный путь до файла
            tries (:obj:`int`, optional): Количество проверок. По умолчанию ``tries=1``

        Returns:
            :obj:`bool`: ``True`` if file exists, ``False`` otherwise

        Examples:
            >>> BaseUtils.is_file_exists("_t1server.settings")
            True
        """
        if os.path.isfile(file_path):
            return True
        for x in xrange(tries - 1):
            time.sleep(1)
            if os.path.isfile(file_path):
                return True
        return False

    @staticmethod
    def is_folder_exists(folder):
        """Проверяет существование папки и доступ на запись.

        Args:
            folder (:obj:`str`): Путь к папке.

        Raises:
            IOError: Если папка не существует

        Examples:
            >>> BaseUtils.is_folder_exists("/test_path")
            IOError: Folder '/test_path' is not exists
        """

        if not os.path.isdir(folder):
            raise IOError("Folder '{}' is not exists".format(folder))

        readme_file = os.path.join(folder, "readme.txt")
        with open(readme_file, "w") as f:
            f.write(
                "If you see this file - Trassir script have no access to remove it!"
            )
        os.remove(readme_file)

    @classmethod
    def is_template_exists(cls, template_name):
        """Проверяет существование шаблона

        Args:
            template_name (:obj:`str`): Имя шаблона

        Returns:
            :obj:`bool`: :obj:`True` если шаблон существует, иначе :obj:`False`
        """
        if template_name in [
            tmpl_.name for tmpl_ in cls._host_api.settings("templates").ls()
        ]:
            return True
        return False

    @classmethod
    def cat(cls, filepath, check_ext=True):
        """Выводит на отображение текстовую инфомрацию.

        Tip:
            - *WinOS*: открывает файл программой по умолчанию
            - *TrassirOS*: открывает файл в терминале с помощью утилиты `cat`

        Note:
            | Доступные расширения файлов: ``[".txt", ".csv", ".log"]``
            | Если открываете файл с другим расширением установите ``check_ext=False``

        Args:
            filepath (:obj:`str`): Полный путь до файла
            check_ext (:obj:`bool`, optional): Если ``True`` - проверяет расширение файла.
                По умолчанию ``True``

        Examples:
            >>> BaseUtils.cat("/home/trassir/ Trassir 3 License.txt")
        .. image:: images/base_utils.cat.png

        Raises:
            :class:`TypeError`: Если ``check_ext=True`` расширение файла нет в списке :obj:`_TEXT_FILE_EXTENSIONS`
        """

        if check_ext:
            _, ext = os.path.splitext(filepath)
            if ext not in cls._TEXT_FILE_EXTENSIONS:
                raise TypeError(
                    "Bad file extension: {}. To ignore this: set check_ext=False".format(
                        ext
                    )
                )

        if os.name == "nt":
            os.startfile(filepath)
        else:
            subprocess.Popen(
                [
                    "xterm -fg black -bg white -geometry 90x35 -fn "
                    "-misc-fixed-medium-r-normal--18-120-100-100-c-90-iso10646-1 -e bash -c \"cat '{}'; "
                    "read -n 1 -s -r -p '\n\nPress any key to exit'; exit\"".format(
                        filepath
                    )
                ],
                shell=True,
                close_fds=True,
            )

    @classmethod
    def _json_serializer(cls, data):
        """JSON serializer for objects not serializable by default"""
        if isinstance(data, (datetime, date)):
            return data.isoformat()

        elif isinstance(data, cls._host_api.ScriptHost.SE_Settings):
            return "settings('{}')".format(data.path)

        elif isinstance(data, cls._host_api.ScriptHost.SE_Object):
            return "object('{}')".format(data.guid)

        return type(data).__name__

    @classmethod
    def to_json(cls, data, **kwargs):
        """Сериализация объекта в JSON стрку

        Note:
            Не вызывает ошибку при сериализации объектов :obj:`datetime`,
            :obj:`date`, :obj:`SE_Settings`, :obj:`SE_Object`

        Args:
            data (:obj:`obj`): Объект для сериализации

        Returns:
            :obj:`str`: JSON строка

        Examples:
            >>> obj = {"now": datetime.now()}
            >>> json.dumps(obj)
            TypeError: datetime.datetime(2019, 4, 2, 18, 01, 33, 881000) is not JSON serializable
            >>> BaseUtils.to_json(obj, indent=None)
            '{"now": "2019-04-02T18:01:33.881000"}'
        """

        return json.dumps(data, default=cls._json_serializer, **kwargs)

    @classmethod
    def lpr_flags_decode(cls, flags):
        """Преобразует флаги события AutoTrassir

        Приводит флаги события человекочитаемый список

        Note:
            Список доступных флагов:

            - ``LPR_UP`` - Направление движения вверх
            - ``LPR_DOWN`` - Направление движения вниз

            - ``LPR_BLACKLIST`` - Номер в черном списке
            - ``LPR_WHITELIST`` - Номер в черном списке
            - ``LPR_INFO`` - Номер в информационном списке

            - ``LPR_FIRST_LANE`` - Автомобиль двигается по первой полосе
            - ``LPR_SECOND_LANE`` - Автомобиль двигается по второй полосе
            - ``LPR_THIRD_LANE`` - Автомобиль двигается по третей полосе

            - ``LPR_EXT_DB_ERROR`` - Ошибка во внешнем списке
            - ``LPR_CORRECTED`` - Номер исправлен оператором

        Args:
            flags (:obj:`int`): Биты LPR события. Как правило аргумент :obj:`ev.flags`
                события :obj:`SE_LprEvent` AutoTrassir. Например :obj:`536870917`

        Returns:
            List[:obj:`str`]: Список флагов

        Examples:
            >>> BaseUtils.lpr_flags_decode(536870917)
            ['LPR_UP', 'LPR_BLACKLIST']
        """
        return [bit for bit, code in cls._LPR_FLAG_BITS.iteritems() if (flags & code)]

    @classmethod
    def image_to_base64(cls, image):
        """Создает base64 из изображения

        Args:
            image (:obj:`str`): Путь к изображению или изображение

        Returns:
            :obj:`str`: Base64 image

        Examples:
            >>> BaseUtils.image_to_base64(r"manual\en\cloud-devices-16.png")
            'iVBORw0KGgoAAAANSUhEUgAAB1MAAAH0CAYAAABo5wRhAAAACXBIWXMAAC4jA...'
            >>> BaseUtils.image_to_base64(open(r"manual\en\cloud-devices-16.png", "rb").read())
            'iVBORw0KGgoAAAANSUhEUgAAB1MAAAH0CAYAAABo5wRhAAAACXBIWXMAAC4jA...'
        """
        _, ext = os.path.splitext(image)

        if ext.lower() in cls._IMAGE_EXT:
            image = cls.win_encode_path(image)
            if not BaseUtils.is_file_exists(image):
                return ""

            with open(image, "rb") as image_file:
                image = image_file.read()

        return base64.b64encode(image)

    @classmethod
    def base64_to_html_img(cls, image_base64, **kwargs):
        """Возвращает base64 изображение в `<img>` html теге

        Args:
            image_base64 (:obj:`str`): Base64 image
            **kwargs: HTML `<img>` tag attributes. Подробнее на `html.com
                <https://html.com/tags/img/#Attributes_of_img>`_

        Returns:
            :obj:`str`: html image

        Examples:
            >>> base64_image = BaseUtils.image_to_base64(r"manual\en\cloud-devices-16.png")
            >>> html_image = BaseUtils.base64_to_html_img(base64_image, width=280, height=75)
            >>> html_image
            '<img src="data:image/png;base64,iVBORw0KGgoAA...Jggg==" width="280" height="75">'
            >>> host.message(html_image)

                .. image:: images/popup_sender.image.png
        """
        html_img = cls._HTML_IMG_TEMPLATE.format(
            img=image_base64,
            attr=" ".join(
                '%s="%s"' % (key, value) for key, value in kwargs.iteritems()
            ),
        )
        return html_img

    @staticmethod
    def save_pkl(file_path, data):
        """Сохраняет данные в `.pkl` файл

        Args:
            file_path (:obj:`str`): Путь до файла
            data: Данные для сохранения

        Returns:
            :obj:`str`: Абсолютный путь до файла

        Examples:
            >>> data = {"key": "value"}
            >>> BaseUtils.save_pkl("saved_data.pkl", data)
            'D:\\DSSL\\Trassir-4.1-Client\\saved_data.pkl'

        """
        if not file_path.endswith(".pkl"):
            file_path = file_path + ".pkl"

        with open(file_path, "wb") as opened_file:
            pickle.dump(data, opened_file)

        return os.path.abspath(file_path)

    @staticmethod
    def load_pkl(file_path, default_type=dict):
        """Загружает данные из `.pkl` файла

        Args:
            file_path (:obj:`str`): Путь до файла
            default_type (optional):
                Тип данных, возвращаемый при неудачной загрузке данных из файла.
                По умолчанию :obj:`dict`

        Returns:
            Данные из файла или :obj:`default_type()`

        Examples:
            >>> BaseUtils.load_pkl("fake_saved_data.pkl")
            {}
            >>> BaseUtils.load_pkl("fake_saved_data.pkl", default_type=list)
            []
            >>> BaseUtils.load_pkl("fake_saved_data.pkl", default_type=int)
            0
            >>> BaseUtils.load_pkl("fake_saved_data.pkl", default_type=str)
            ''
            >>> BaseUtils.load_pkl("saved_data.pkl")
            {'key': 'value'}
        """

        if not file_path.endswith(".pkl"):
            file_path = file_path + ".pkl"

        data = default_type()

        if os.path.isfile(file_path):
            try:
                with open(file_path, "rb") as opened_file:
                    data = pickle.load(opened_file)
            except (EOFError, IndexError, ValueError, TypeError):
                """ dump file is empty or broken """

        return data

    @classmethod
    def get_object(cls, obj_id):
        """Возвращает объект Trassir, если он доступен, иначе ``None``

        Args:
            obj_id (:obj:`str`): Guid объекта или его имя

        Returns:
            :obj:`ScriptHost.SE_Object`: Объект Trassir или ``None``

        Examples:
            >>> obj = BaseUtils.get_object("EZJ4QnbC")
            >>> if obj is None:
            >>>     host.error("Object not found")
            >>> else:
            >>>     host.message("Object name is {0.name}".format(obj))
        """
        if not isinstance(obj_id, (str, unicode)):
            raise TypeError(
                "Expected str or unicode, got '{}'".format(type(obj_id).__name__)
            )
        obj = cls._host_api.object(obj_id)
        try:
            obj.name
        except EnvironmentError:
            """Object not found"""
            obj = None
        return obj

    @classmethod
    def get_object_name_by_guid(cls, guid):
        """Возвращает имя объекта Trassir по его guid

        Tip:
            Можно использовать:

            - guid объекта ``"CFsuNBzt"``
            - guid объекта + guid сервера ``"CFsuNBzt_pV4ggECb"``

        Args:
            guid (:obj:`str`): Guid объекта Trassir

        Returns:
            :obj:`str`: Имя объекта, если объект найден, иначе ``guid``

        Examples:
            >>> BaseUtils.get_object_name_by_guid("EZJ4QnbC")
            'AC-D2141IR3'
            >>> BaseUtils.get_object_name_by_guid("EZJ4QnbC-")
            'EZJ4QnbC-'
        """
        guid = guid.split("_", 1)[0]
        obj = cls.get_object(guid)
        if obj is None:
            name = guid
        else:
            name = obj.name
        return name

    @classmethod
    def get_full_guid(cls, obj_id):
        """Возвращает полный guid объекта

        Args:
            obj_id (:obj:`str`): Guid объекта или его имя

        Returns:
            :obj:`str`: Полный guid объекта
        """

        tr_obj = cls.get_object(obj_id)
        if tr_obj is not None:
            for obj in cls._host_api.objects_list(""):
                if tr_obj.guid == obj[1]:
                    return "{}_{}".format(obj[1], cls._FOLDERS.get(obj[3], obj[3]))

    @classmethod
    def get_operator_gui(cls):
        """Возвращает объект интерфейса оператора

        Returns:
            :obj:`OperatorGUI`: Объект интерфейса оператора

        Raises:
            ScriptError: Если не удается загрузить интерфейс

        Examples:
            Открыть интерфейс Trassir а мониторе №1

            >>> operator_gui = BaseUtils.get_operator_gui()
            >>> operator_gui.raise_monitor(1)
        """
        obj = cls.get_object("operatorgui_{}".format(cls._host_api.settings("").guid))
        if obj is None:
            raise ScriptError("Failed to load operator gui")
        return obj

    @classmethod
    def get_server_guid(cls):
        """Возвращает guid текущего сервра

        Returns:
            :obj:`str`: Guid сервера

        Examples:
            >>> BaseUtils.get_server_guid()
            'client'
        """
        return cls._host_api.settings("").guid

    @classmethod
    def get_script_name(cls):
        """Возвращает имя текущего скрипта

        Returns:
            :obj:`str`: Имя скрипта

        Examples:
            >>> BaseUtils.get_script_name()
            'Новый скрипт'
        """
        return cls._host_api.stats().parent()["name"] or __name__

    @classmethod
    def get_screenshot_folder(cls):
        """Возвращает путь до папки скриншотов

        При этом производит проверку папки методом
        :meth:`BaseUtils.is_folder_exists`

        Returns:

            :obj:`str`: Полный путь к папке скриншотов

        Examples:
            >>> BaseUtils.get_screenshot_folder()
            '/home/trassir/shots'
        """
        folder = cls._host_api.settings("system_wide_options")["screenshots_folder"]
        cls.is_folder_exists(folder)
        return folder

    @classmethod
    def get_logger(
        cls,
        name=None,
        host_log="WARNING",
        popup_log="ERROR",
        file_log=None,
        file_name=None,
    ):
        """Возвращает логгер с предустановленными хэндлерами

        Доступные хэндлеры:
            - *host_log*: Пишет сообщения в основной лог сервера _t1server.log
            - *popup_log*: Показывает всплывающие сообщения ``message/alert/error``
            - *file_log*: Пишет сообщения в отдельный файл в папку скриншотов

        Для каждого хэндлера можно установить разный уровень логирования

        По умолчанию ``host_log="WARNING"`` и ``popup_log="ERROR"``

        Note:
            Имя файла лога можно указать с расширение ".log" или без.

        See Also:
            `Logging levels на сайте docs.python.org
            <https://docs.python.org/2/library/logging.html#logging-levels>`_

        Args:
            name (:obj:`str`, optional): Имя логгера, должно быть уникальным для
                каждого скрипта. По умолчанию :obj:`None`, и равно guid скрипта.
            host_log (:obj:`str`, optional): Уровень логирования в основной лог.
                По умолчанию ``"WARNING"``
            popup_log (:obj:`str`, optional): Уровень логирования во всплывающих
                сообщениях. По умолчанию ``"ERROR"``
            file_log (:obj:`str`, optional): Уровень логирования в отдельный файл
                По умолчанию :obj:`None`
            file_name (:obj:`str`, optional): Имя файла для логирования.
                По умолчанию :obj:`None` и равно ``<имени скрипта>.log``

        Returns:
            :obj:`logging.logger`: Логгер

        Examples:
            >>> logger = BaseUtils.get_logger()
            >>> logger.warning("My warning message")
            >>> try:
            >>>     do_something()
            >>> except NameError:
            >>>     logger.error("Function is not defined", exc_info=True)
        """
        logger_ = logging.getLogger(name or __name__)
        logger_.setLevel("DEBUG")

        if logger_.handlers:
            for handler in logger_.handlers[:]:
                handler.close()
                logger_.removeHandler(handler)

        if host_log:
            host_handler = HostLogHandler()
            host_handler.setLevel(host_log)
            if name:
                host_formatter = logging.Formatter(
                    "[%(levelname)-8s] %(lineno)-4s <%(funcName)s> - %(name)s: %(message)s"
                )
            else:
                host_formatter = logging.Formatter(
                    "[%(levelname)-8s] %(lineno)-4s <%(funcName)s> - %(message)s"
                )
            host_handler.setFormatter(host_formatter)
            logger_.addHandler(host_handler)

        if popup_log:
            popup_handler = PopupHandler()
            popup_handler.setLevel(popup_log)
            popup_formatter = logging.Formatter(
                fmt="<b>[%(levelname)s]</b> Line: %(lineno)s<br><i>%(message).630s</i>"
            )
            popup_handler.setFormatter(popup_formatter)
            logger_.addHandler(popup_handler)

        if file_log:
            if file_name is None:
                file_name = cls.get_script_name()

            if not file_name.endswith(".log"):
                file_name = "{}.log".format(file_name)

            file_path = os.path.join(cls.get_screenshot_folder(), file_name)
            file_path = cls.win_encode_path(file_path)

            file_handler = MyFileHandler(file_path)
            file_handler.setLevel(file_log)
            if name:
                file_formatter = logging.Formatter(
                    fmt="%(asctime)s [%(levelname)-8s] %(lineno)-4s <%(funcName)s> - %(name)s: %(message)s",
                    datefmt="%Y/%m/%d %H:%M:%S",
                )
            else:
                file_formatter = logging.Formatter(
                    fmt="%(asctime)s [%(levelname)-8s] %(lineno)-4s <%(funcName)s> - %(message)s",
                    datefmt="%Y/%m/%d %H:%M:%S",
                )
            file_handler.setFormatter(file_formatter)
            logger_.addHandler(file_handler)

        return logger_

    @classmethod
    def set_script_name(cls, fmt=None):
        """Автоматически изменяет имя скрипта

        Новое имя скрипта создается на основе `параметров
        <https://www.dssl.ru/files/trassir/manual/ru/setup-script-parameters.html>`_
        скрипта. По желанию можно изменить шаблон имени. По умолчанию
        :obj:`"[{company}] {title} v{version}"`

        Note:
            Имя изменяется только если сейчас у скрипта стандартное имя,
            например :obj:`"Новый скрипт"` или :obj:`"Unnamed Script"` и др.

        Args:
            fmt (:obj:`str`, optional): Шаблон имени скрипта. По умолчанию :obj:`None`

        Examples:
            >>> BaseUtils.set_script_name()
            'AATrubilin - trassir_script_framework v0.4'

            >>> BaseUtils.set_script_name(fmt="{title}")
            'trassir_script_framework'
        """
        if __doc__:
            if cls._host_api.stats().parent()["name"] in cls._SCR_DEFAULT_NAMES:
                try:
                    root = ElementTree.fromstring(__doc__)
                except ElementTree.ParseError:
                    root = None

                company = root.find("company") if root else None
                title = root.find("title") if root else None
                version = root.find("version") if root else None

                if fmt is None:
                    fmt = "[{company}] {title} v{version}"

                script_name = fmt.format(
                    company="DSSL" if company is None else company.text,
                    title="Script" if title is None else title.text,
                    version="0.1" if version is None else version.text,
                )

                cls._host_api.stats().parent()["name"] = script_name

                return script_name


class Worker(threading.Thread):
    """Thread executing tasks from a given tasks queue"""

    def __init__(self, tasks):
        super(Worker, self).__init__()
        self.tasks = tasks
        self.daemon = True
        self.start()

    def run(self):
        while True:
            func, args, kargs = self.tasks.get()
            try:
                func(*args, **kargs)
            finally:
                self.tasks.task_done()


class ThreadPool:
    """Pool of threads consuming tasks from a queue"""

    def __init__(self, num_threads, callback=None, host_api=host):
        self._host_api = host_api
        self.tasks = Queue(num_threads)
        for _ in xrange(num_threads):
            Worker(self.tasks)
        if callback is None:
            callback = BaseUtils.do_nothing
        self.callback = callback

    def add_task(self, func, *args, **kargs):
        """Add a task to the queue"""
        self.tasks.put((func, args, kargs))

    def wait_completion(self):
        """Wait for completion of all the tasks in the queue"""
        self.tasks.join()
        self._host_api.timeout(1000, self.callback)


class HTTPRequester(py_object):
    """Framework for urllib2

    See Also:
        https://docs.python.org/2/library/urllib2.html#urllib2.build_opener

    Args:
        opener (:obj:`urllib2.OpenerDirector`, optional): Обработчик запросов.
            По умолчанию :obj:`None`
        timeout (:obj:`int`, optional): Время ожидания запроса, в секундах.
            По умолчанию :obj:`timeout=10`

    Examples:
        Пример запроса к SDK Trassir

        >>> # Отключение проверки сертификата
        >>> context = ssl.create_default_context()
        >>> context.check_hostname = False
        >>> context.verify_mode = ssl.CERT_NONE
        >>>
        >>> handler = urllib2.HTTPSHandler(context=context)
        >>> opener = urllib2.build_opener(handler)
        >>>
        >>> requests = HTTPRequester(opener, timeout=20)
        >>> response = requests.get(
        >>>     "https://172.20.0.101:8080/login",
        >>>     params={"username": "Admin", "password": "12345"}
        >>> )
        >>>
        >>> response.code
        200
        >>> response.text
        '{\\n   "sid" : "T6LAAcxg",\\n   "success" : 1\\n}\\n'
        >>> response.json
        {u'success': 1, u'sid': u'T6LAAcxg'}
    """

    class Response(py_object):
        """Класс ответа от сервера

        Attributes:
            code (:obj:`str` | :obj:`int`): Код ответа сервера
            text (:obj:`str`): Текст ответа
            json (:obj:`dict` | :obj:`list`): Создает объект из json ответа
        """

        def __init__(self, *args):
            self.code, self.text = args

        @property
        def json(self):
            return json.loads(self.text)

    def __init__(self, opener=None, timeout=10):
        if opener is None:
            handler = urllib2.BaseHandler()
            opener = urllib2.build_opener(handler)
        self._opener = opener

        self.timeout = timeout

    @BaseUtils.catch_request_exceptions
    def _get_response(self, request):
        """Returns response

        Args:
            request (:obj:`urllib2.Request`): This class is an abstraction of a URL request
        """
        response = self._opener.open(request, timeout=self.timeout)
        return response.code, response.read()

    @staticmethod
    def _parse_params(**params):
        """Params get string params

        Args:
            **params (dict): Keyword arguments

        Returns:
            str: params string
        """
        return "&".join(
            "{key}={value}".format(key=key, value=value)
            for key, value in params.iteritems()
        )

    @staticmethod
    def _prepare_headers(headers):
        """Prepare headers for request"""
        if headers is None:
            headers = {}

        if "User-Agent" not in headers:
            headers["User-Agent"] = "TrassirScript"
        return headers

    def get(self, url, params=None, headers=None):
        """Создает GET запрос по указанному :obj:`url`

        Args:
            url (:obj:`str`): Url для запроса
            params (:obj:`dict`, optional): Параметры GET запроса
            headers (:obj:`dict`, optional): Заголовки запроса

        Examples:
            >>> requests = HTTPRequester()
            >>> response = requests.get(
            >>>     "http://httpbin.org/get",
            >>>     params={"PARAMETER": "TEST"},
            >>> )
            >>> response.code
            200
            >>> response.text
            '{\\n  "args": {\\n    "PARAMETER": "TEST"\\n  }, \\n ...'
            >>> response.json
            {u'args': {u'PARAMETER': u'TEST'}, ...}

        Returns:
            :class:`HTTPRequester.Response`: Response instance
        """
        if params is not None:
            url += "?{params}".format(params=self._parse_params(**params))

        headers = self._prepare_headers(headers)

        request = urllib2.Request(url, headers=headers)
        response = self._get_response(request)
        return self.Response(*response)

    def post(self, url, data=None, headers=None):
        """Создает POST запрос по указанному :obj:`url`

        Args:
            url (:obj:`str`): Url для запроса
            data (:obj:`dict`, optional): Данные POST запроса
            headers (:obj:`dict`, optional): Заголовки запроса

        Examples:
            >>> requests = HTTPRequester()
            >>> response = requests.post(
            >>>     "http://httpbin.org/post",
            >>>     data={"PARAMETER": "TEST"},
            >>>     headers={"Content-Type": "application/json"},
            >>> )
            >>> response.code
            200
            >>> response.text
            '{\\n  "args": {\\n    "PARAMETER": "TEST"\\n  }, \\n ...'
            >>> response.json
            {u'args': {u'PARAMETER': u'TEST'}, ...}

        Returns:
            :class:`HTTPRequester.Response`: Response instance
        """
        if data is None:
            data = {}

        if isinstance(data, dict):
            data = urllib.urlencode(data)

        headers = self._prepare_headers(headers)

        request = urllib2.Request(url, data=data, headers=headers)
        response = self._get_response(request)
        return self.Response(*response)


class ScriptObject(host.TrassirObject, py_object):
    """Создает объект для генерации событий

    Args:
        name (:obj:`str`, optional): Имя объекта. По умолчанию :obj:`None`
        guid (:obj:`str`, optional): Guid объекта. По умолчанию :obj:`None`
        parent (:obj:`str`, optional): Guid родительского объекта. По умолчанию :obj:`None`

    Note:
        - Имя объекта по умолчанию - :meth:`BaseUtils.get_script_name`
        - Guid объекта по умолчанию строится по шаблноу ``"{script_guid}_object"``
        - Guid родительского объекта по умолчанию -
          :meth:`BaseUtils.get_server_guid`

    Examples:
        >>> # Создаем объект
        >>> scr_obj = ScriptObject()

        >>> # Проверяем текущее состояние объекта
        >>> scr_obj.health
        'OK'

        >>> # Установить флаг возле объекта
        >>> scr_obj.check_me = True

        >>> # Сгенерировать событие с текстом
        >>> scr_obj.fire_event_v2("New event")
    """

    def __init__(self, name=None, guid=None, parent=None, host_api=host):
        super(ScriptObject, self).__init__("Script")

        self._host_api = host_api
        scr_parent = host_api.stats().parent()

        self._name = name or BaseUtils.get_script_name()
        self.set_name(self._name)

        self._guid = guid or "{}-object".format(scr_parent.guid)
        self.set_guid(self._guid)

        self._parent = parent or BaseUtils.get_server_guid()
        self.set_parent(self._parent)

        self._folder = ""

        self._health = "OK"
        self._check_me = True

        self.set_initial_state([self._health, self._check_me])

        host_api.object_add(self)

    @property
    def health(self):
        """:obj:`"OK"` | :obj:`"Error"`: Состояние объекта"""
        return self._health

    @health.setter
    def health(self, value):
        if value in ["OK", "Error"]:
            self.set_state([value, self._check_me])
            self._health = value
        else:
            raise ValueError("Expected 'OK' or 'Error', got '{}'".format(value))

    @property
    def check_me(self):
        """:obj:`bool`: Флаг ``check_me`` объекта"""
        return self._check_me

    @check_me.setter
    def check_me(self, value):
        if isinstance(value, bool) or value in [1, 0]:
            value = 1 - value
            self.set_state([self._health, value])
            self._check_me = value
        else:
            raise ValueError("Expected bool or 1|0, got '{}'".format(value))

    @property
    def name(self):
        """:obj:`str`: Имя объекта"""
        return self._name

    @name.setter
    def name(self, value):
        if isinstance(value, str):
            self.set_name(value)
            self._name = value
        else:
            raise ValueError("Expected str, got {}".format(type(value).__name__))

    @property
    def folder(self):
        """:obj:`str`: Папка объекта"""
        return self._folder

    @folder.setter
    def folder(self, value):
        if not value:
            raise ValueError("Object guid can't be empty")

        if isinstance(value, str):
            if self._folder:
                self.change_folder(value)
            else:
                self.set_folder(value)
            self._folder = value
        else:
            raise ValueError("Expected str, got {}".format(type(value).__name__))

    def fire_event_v2(self, message, channel="", data=""):
        """Создает событие в Trassir

        Args:
            message (:obj:`str`): Сообщение события (``p1``)
            channel (:obj:`str`, optional): Ассоциированный с событием канал (``p2``)
            data (:obj:`str`, optional): Дополнительные данные (``p3``)
        """
        if not isinstance(data, str):
            data = BaseUtils.to_json(data, indent=None)

        self.fire_event("Script: %1", message, channel, data)


class ShotSaverError(ScriptError):
    """Base ShotSaver Exception"""

    pass


class ShotSaver(py_object):
    """Класс для сохранения скриншотов

    Examples:

        >>> ss = ShotSaver()
        >>> # Смена папки сохранения скриншотов по умолчанию
        >>> ss.screenshots_folder
        '/home/trassir/shots'
        >>> ss.screenshots_folder += "/my_shots"
        >>> ss.screenshots_folder
        '/home/trassir/shots/my_shots'
        >>>
        >>> # Сохранение скриншота с канала ``"e80kgBLh_pV4ggECb"``
        >>> ss.shot("e80kgBLh_pV4ggECb")
        '/home/trassir/shots/AC-D2141IR3 Склад (2019.04.03 15-58-26).jpg'
    """

    _AWAITING_FILE = 5  # Time for check file function, sec
    _SHOT_NAME_TEMPLATE = (
        "{name} (%Y.%m.%d %H-%M-%S).jpg"
    )  # Template for shot file name
    _ASYNC_SHOT_TRIES = 2  # Tries to make shot in async_shot method

    def __init__(self, host_api=host):
        self._host_api = host_api
        self._screenshots_folder = BaseUtils.get_screenshot_folder()

    @property
    def screenshots_folder(self):
        """:obj:`str`: Папка для сохранения скриншотов по умолчанию

        Устанавливает новый путь по умолчанию для сохранения скриншотов,
        если папка не существует - создает папку. Или возвращает текущий
        путь для сохранения скриншотов.

        Note:
            По молчанию :obj:`screenshots_folder`  =
            :meth:`BaseUtils.get_screenshot_folder`

        Raises:
            OSError: Если возникает ошибка при создании папки
        """
        return self._screenshots_folder

    @screenshots_folder.setter
    def screenshots_folder(self, folder):
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError as err:
                raise OSError("Can't make dir '{}': {}".format(folder, err))

        self._screenshots_folder = folder

    def shot(self, channel_full_guid, dt=None, file_name=None, file_path=None):
        """Делает скриншот с указанного канала

        Note:
            По умолчанию:

            - :obj:`dt=datetime.now()`
            - :obj:`file_name="{name} (%Y.%m.%d %H-%M-%S).jpg"`, где ``{name}`` - имя канала

        Args:
            channel_full_guid (:obj:`str`): Полный guid анала. Например: ``"CFsuNBzt_pV4ggECb"``
            dt (:obj:`datetime.datetime`, optional): :obj:`datetime.datetime` для скриншота.
                По умолчанию :obj:`None`
            file_name (:obj:`str`, optional): Имя файла с расширением. По умолчанию :obj:`None`
            file_path (:obj:`str`, optional): Путь для сохранения скриншота. По умолчанию :obj:`None`

        Returns:
            :obj:`str`: Полный путь до скриншота

        Raises:
            ValueError: Если в guid канала отсутствует guid сервера
            TypeError: Если ``isinstance(dt, (datetime, date)) is False``

        Examples:
            >>> ss = ShotSaver()
            >>> ss.shot("e80kgBLh_pV4ggECb")
            '/home/trassir/shots/AC-D2141IR3 Склад (2019.04.03 15-58-26).jpg'
        """
        if "_" not in channel_full_guid:
            raise ValueError(
                "Expected full channel guid, got {}".format(channel_full_guid)
            )

        if dt is None:
            ts = "0"
            dt = datetime.now()
        else:
            if not isinstance(dt, (datetime, date)):
                raise TypeError("Expected datetime, got {}".format(type(dt).__name__))
            ts = dt.strftime("%Y%m%d_%H%M%S")

        if file_name is None:
            file_name = dt.strftime(
                self._SHOT_NAME_TEMPLATE.format(
                    name=BaseUtils.get_object_name_by_guid(channel_full_guid)
                )
            )
        if file_path is None:
            file_path = self.screenshots_folder

        self._host_api.screenshot_v2_figures(
            channel_full_guid, file_name, file_path, ts
        )

        return os.path.join(file_path, file_name)

    def _async_shot(
        self, channel_full_guid, dt=None, file_name=None, file_path=None, callback=None
    ):
        """Вызывает ``callback`` после сохнанения скриншота

        * Метод работает в отдельном потоке
        * Вызывает функцию :meth:`ShotSaver.shot`
        * Ждет выполнения функции :meth:`BaseUtils.check_file` ``tries=10``
        * Вызвает ``callback`` функцию

        Args:
            channel_full_guid (:obj:`str`): Полный guid канала. Например: ``"CFsuNBzt_pV4ggECb"``
            dt (:obj:`datetime.datetime`, optional): :obj:`datetime.datetime` для скриншота.
                По умолчанию :obj:`None`
            file_name (:obj:`str`, optional): Имя файла с расширением. По умолчанию :obj:`None`
            file_path (:obj:`str`, optional): Путь для сохранения скриншота. По умолчанию :obj:`None`
            callback (:obj:`function`): Callable function
        """
        if callback is None:
            callback = BaseUtils.do_nothing

        shot_file = ""
        for _ in xrange(self._ASYNC_SHOT_TRIES):
            shot_file = self.shot(
                channel_full_guid, dt=dt, file_name=file_name, file_path=file_path
            )
            if BaseUtils.is_file_exists(
                BaseUtils.win_encode_path(shot_file), self._AWAITING_FILE
            ):
                self._host_api.timeout(100, lambda: callback(True, shot_file))
                break
        else:
            self._host_api.timeout(100, lambda: callback(False, shot_file))

    @BaseUtils.run_as_thread_v2()
    def async_shot(
        self, channel_full_guid, dt=None, file_name=None, file_path=None, callback=None
    ):
        """async_shot(channel_full_guid, dt=None, file_name=None, file_path=None, callback=None)
        Вызывает ``callback`` после сохнанения скриншота

        * Метод работает в отдельном потоке
        * Вызывает функцию :meth:`ShotSaver.shot`
        * Ждет выполнения функции :meth:`BaseUtils.check_file` ``tries=10``
        * Вызвает ``callback`` функцию

        Args:
            channel_full_guid (:obj:`str`): Полный guid канала. Например: ``"CFsuNBzt_pV4ggECb"``
            dt (:obj:`datetime.datetime`, optional): :obj:`datetime.datetime` для скриншота.
                По умолчанию :obj:`None`
            file_name (:obj:`str`, optional): Имя файла с расширением. По умолчанию :obj:`None`
            file_path (:obj:`str`, optional): Путь для сохранения скриншота. По умолчанию :obj:`None`
            callback (:obj:`function`, optional): Callable function

        Returns:
            :obj:`threading.Thread`: Thread object

        Examples:
            >>> def callback(success, shot_path):
            >>>     # Пример callback функции
            >>>     # Args:
            >>>     #     success (bool): True если скриншот успешно сохранен, иначе False
            >>>     #     shot_path (str): Полный путь до скриншота
            >>>     if success:
            >>>         host.message("Скриншот успешно сохранен<br>%s" % shot_path)
            >>>     else:
            >>>         host.error("Ошибка сохранения скриншота <br>%s" % shot_path)
            >>>
            >>> ss = ShotSaver()
            >>> ss.async_shot("e80kgBLh_pV4ggECb", callback=callback)
            <Thread(Thread-76, started daemon 212)>
        """
        self._async_shot(
            channel_full_guid,
            dt=dt,
            file_name=file_name,
            file_path=file_path,
            callback=callback,
        )

    @BaseUtils.run_as_thread_v2()
    def pool_shot(self, shot_args, pool_size=10, end_callback=None):
        """pool_shot(shot_args, pool_size=10, end_callback=None)
        Сохраняет скриншоты по очереди.

        Одновременно в работе не более :obj:`pool_size` задачь.
        Вызывает ``callback`` после сохнанения всех скриншотов
        см. :meth:`ShotSaver.async_shot`

        Args:
            shot_args (List[:obj:`tuple`]): Аргументы для функции async_shot
            pool_size (:obj:`int`, optional): Размер пула. По умолчанию :obj:`pool_size=10`
            end_callback (:obj:`function`, optional): Вызывается после сохранения всех скриншотов

        Returns:
            :obj:`threading.Thread`: Thread object

        Examples:
            >>> from datetime import datetime, timedelta
            >>>
            >>> def end_callback():
            >>>     host.message("Все скриншоты сохранены")
            >>>
            >>> channel_guid = "e80kgBLh_pV4ggECb"
            >>> dt_now = datetime.now()
            >>> dt_range = [dt_now - timedelta(minutes=10*i) for i in xrange(12)]
            >>> dt_range
            [datetime.datetime(2019, 4, 25, 9, 38, 9, 253000), datetime.datetime(2019, 4, 25, 9, 28, 9, 253000), ...]
            >>> shot_args = []
            >>> for dt in dt_range:
            >>>     kwargs = {"dt": dt}
            >>>     shot_args.append(((channel_guid,), kwargs))
            >>>
            >>> ss = ShotSaver()
            >>> ss.pool_shot(shot_args, pool_size=10, end_callback=end_callback)
            <Thread(Thread-76, started daemon 212)>
        """
        pool = ThreadPool(pool_size, end_callback)
        for args, kwargs in shot_args:
            pool.add_task(self._async_shot, *args, **kwargs)
        pool.wait_completion()
        return pool


class VideoExporterError(ScriptError):
    """Base ShotSaver Exception"""

    pass


class VideoExporter(py_object):
    """Класс для экспорта видео

    Examples:
        Смена папки экспорта видео по умолчанию

        >>> ss = VideoExporter()
        >>> ss.export_folder
        '/home/trassir/shots'
        >>> ss.export_folder += "/my_videos"
        >>> ss.export_folder
        '/home/trassir/shots/my_videos'

        | Экспорт видео с вызовом ``callback`` функции после выполнения.
        | Начало экспорта - 120 секунд назад, продолжительность 60 сек.

        >>> def callback(success, file_path, channel_full_guid):
        >>>     # Пример callback функции
        >>>     # Args:
        >>>     #     success (bool): True если видео экспортировано успешно, иначе False
        >>>     #     file_path (str): Полный путь до видеофайла
        >>>     #     channel_full_guid (str) : Полный guid канала
        >>>     if success:
        >>>         host.message("Экспорт успешно завершен<br>%s" % file_path)
        >>>     else:
        >>>         host.error("Ошибка экспорта<br>%s" % file_path)

        >>> ss = VideoExporter()
        >>> dt_start = datetime.now() - timedelta(seconds=120)
        >>> ss.export(callback, "e80kgBLh_pV4ggECb", dt_start)
    """

    _EXPORTED_VIDEO_NAME_TEMPLATE = (
        "{name} ({dt_start} - {dt_end}){sub}.avi"
    )  # Template for shot file name

    def __init__(self, host_api=host):
        self._host_api = host_api
        self._export_folder = BaseUtils.get_screenshot_folder()
        self._now_exporting = False
        self._queue = deque()
        self._default_prebuffer = host_api.settings("archive")["prebuffer"] + 2

    @property
    def export_folder(self):
        """:obj:`str`: Папка для экспорта видео по умолчанию

        Устанавливает новый путь по умолчанию для экспорта видео,
        если папка не существует - создает папку. Или возвращает текущий
        путь для экспорта видео.

        Note:
            По молчанию ``export_folder`` = :meth:`BaseUtils.get_screenshot_folder`

        Raises:
            OSError: Если возникает ошибка при создании папки
        """
        return self._export_folder

    @export_folder.setter
    def export_folder(self, folder):
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError as err:
                raise OSError("Can't make dir '{}': {}".format(folder, err))

        self._export_folder = folder

    def _get_prebuffer(self, server_guid, dt_end):
        """Get prebuffer delay

        Args:
            server_guid (str): Full channel guid include server guid

        Returns:
            int: Prebuffer delay
        """
        setting_path = "/{}/archive".format(server_guid)

        try:
            prebuffer = self._host_api.settings(setting_path)["prebuffer"] + 2
        except KeyError:
            prebuffer = self._default_prebuffer

        wait_dt_end = (int(time.mktime(dt_end.timetuple())) + prebuffer) * 1000000

        return "%.0f" % wait_dt_end

    def clear_complete_tasks(self):
        for task in self._host_api.archive_export_tasks_get():
            if task["state"] != 1:
                self._host_api.archive_export_task_cancel(
                    task["id"],  # task id from archive_export_tasks_get
                    -1,  # -1 - do not wait for result, 0 - wait forever, > 0 - wait timeout_sec seconds
                    BaseUtils.do_nothing,  # callback_success
                    BaseUtils.do_nothing,  # callback_error
                )

    def _check_queue(self):
        self._host_api.timeout(10, self.clear_complete_tasks)
        if self._queue:
            args, kwargs = self._queue.popleft()
            self._export(*args, **kwargs)

    def _export_checker(self, status, callback, file_path, channel_full_guid):
        if status == 1:
            return
        elif status in [0, 2]:
            """Export failed"""
            self._host_api.timeout(
                100, lambda: callback(False, file_path, channel_full_guid)
            )
        else:
            """Export success"""
            self._host_api.timeout(
                100, lambda: callback(True, file_path, channel_full_guid)
            )

        self._now_exporting = False
        self._check_queue()

    def _export(
        self,
        channel_full_guid,
        dt_start,
        dt_end=None,
        duration=60,
        prefer_substream=False,
        file_name=None,
        file_path=None,
        callback=None,
    ):
        """Exporting file

        Call callback(success: bool, file_path: str, channel_full_guid: str)
        when export finished, and clear tasks in trassir main control panel

        Note:
            Export task adding only when previous task finished
            You can set dt_start, dt_end, or dt_start, duration for export
            if dt_end is None: dt_end = dt_start + timedelta(seconds=duration)

        Args:
            channel_full_guid (str): Full channel guid; example: "CFsuNBzt_pV4ggECb"
            dt_start (datetime): datetime instance for export start
            dt_end (datetime, optional): datetime instance for export end; default: None
            duration (int, optional): Export duration (dt_start + duration seconds) if dt_end is None; default: 10
            prefer_substream (bool, optional): If True - export substream; default: False
            file_name (str, optional): File name with extension; default: _EXPORTED_VIDEO_NAME_TEMPLATE
            file_path (str, optional): Path to save shot; default: screenshots_folder
            callback (function, optional): Function that calling when export finished
        """

        if "_" not in channel_full_guid:
            raise ValueError(
                "Expected full channel guid, got {}".format(channel_full_guid)
            )

        if not isinstance(dt_start, (datetime, date)):
            raise TypeError("Expected datetime, got {}".format(type(dt_start).__name__))

        if dt_end:
            if not isinstance(dt_end, (datetime, date)):
                raise TypeError(
                    "Expected datetime, got {}".format(type(dt_end).__name__)
                )
        else:
            dt_end = dt_start + timedelta(seconds=duration)

        ts_start = "%.0f" % (time.mktime(dt_start.timetuple()) * 1000000)
        ts_end = "%.0f" % (time.mktime(dt_end.timetuple()) * 1000000)

        channel_guid, server_guid = channel_full_guid.split("_")

        options = {
            "prefer_substream": prefer_substream,
            "postponed_until_ts": self._get_prebuffer(server_guid, dt_end),
        }

        if file_name is None:
            file_name = self._EXPORTED_VIDEO_NAME_TEMPLATE.format(
                name=BaseUtils.get_object_name_by_guid(channel_guid),
                dt_start=dt_start.strftime("%Y.%m.%d %H-%M-%S"),
                dt_end=dt_end.strftime("%Y.%m.%d %H-%M-%S"),
                sub="_sub" if prefer_substream else "",
            )

        if file_path is None:
            file_path = self.export_folder

        exporting_path = os.path.join(file_path, file_name)

        if callback is None:
            callback = BaseUtils.do_nothing

        self._now_exporting = True

        def checker(status):
            self._export_checker(status, callback, exporting_path, channel_full_guid)

        self._host_api.archive_export(
            server_guid,
            channel_guid,
            exporting_path,
            ts_start,
            ts_end,
            options,
            checker,
        )

    def export(
        self,
        channel_full_guid,
        dt_start,
        dt_end=None,
        duration=60,
        prefer_substream=False,
        file_name=None,
        file_path=None,
        callback=None,
    ):
        """Запускает экспорт или добавляет задачу экспорта в очередь.

        После завершения экспорта вызывает ``callback`` функцию
        а также очищает список задач экспорта в панеле управления Trassir.

        Note:
            Задача экспорта добавляется только после завершения предыдущей.

        Tip:
            - Вы можете задать время начала и окончания экспорта
              ``dt_start``, ``dt_end``.
            - Или можно задать время начала экспорта ``dt_start`` и
              продолжительность экспорта (в сек.) ``duration``. По умолчнию
              ``duration=60``.
            - Если ``dt_end=None`` фунция использует ``duration`` для вычисления
              времени окончания ``dt_end = dt_start + timedelta(seconds=duration)``.

        Args:
            channel_full_guid (:obj:`str`): Полный guid канала. Например: ``"CFsuNBzt_pV4ggECb"``
            dt_start (:obj:`datetime.datetime`): :obj:`datetime.datetime` начала экспорта
            dt_end (:obj:`datetime.datetime`, optional): :obj:`datetime.datetime` окончания экспорта.
                По умолчанию :obj:`None`
            duration (:obj:`int`, optional): Продолжительность экспорта, в секундах. Используется если
                ``dt_end is None``. По умолчанию ``60``
            prefer_substream (:obj:`bool`, optional): Если ``True`` - Экспортирует субпоток.
                По умолчанию ``False``
            file_name (:obj:`str`, optional): Имя экспортируемого файла. По умолчанию :obj:`None`
            file_path (:obj:`str`, optional): Путь для экспорта. По умолчанию :obj:`None`
            callback (:obj:`function`, optional): Функция, которая вызывается после завершения экспорта.
                По умолчанию :obj:`None`
        """

        args = (channel_full_guid, dt_start)
        kwargs = {
            "dt_end": dt_end,
            "duration": duration,
            "prefer_substream": prefer_substream,
            "file_name": file_name,
            "file_path": file_path,
            "callback": callback,
        }
        if self._now_exporting:
            self._queue.append((args, kwargs))
        else:
            self._export(*args, **kwargs)


class TemplateError(ScriptError):
    """Raised by Template class"""

    pass


class GUITemplate(py_object):
    """Класс для работы с шаблонами Trassir

    При инициализации находит существующий шаблон по имени или создает новый.

    Note:
        Если вручную создать два или большее шаблона с одинаковыми именами
        данный класс выберет первый попавшийся шаблон с заданным именем.

    Warning:
            Работа с контентом шаблона может привести к падениям трассира.
            Используйте данный класс на свой страх и риск!

    Tip:
        Для понимания, как формируется контент отредактируйте любой шаблон
        вручную и посмотрите что получится в скрытых параметрах трассира
        (активируются нажатием клавиши F4 в настройках трассира)
        `Настройки/Шабоны/<Имя шаблона>/content`

        Ниже предсталвены некоторые примеры шаблонов

        - Вывод одного канала ``S0tE8nfg_Or3QZu4D``
          :obj:`gui7(DEWARP_SETTINGS,zwVj07w0,dewarp(),1,S0tE8nfg_Or3QZu4D)`
        - Вывод шаблона 4х4 с каналами двумя ``Kpid6EC0_Or3QZu4D``, ``ZRtXLrgu_Or3QZu4D``
          :obj:`gui7(DEWARP_SETTINGS,zwVj07w0,dewarp(),4,Kpid6EC0_Or3QZu4D,ZRtXLrgu_Or3QZu4D,,)`
        - Вывод шаблон с минибраузером и ссылкой на https://www.google.com/
          :obj:`minibrowser(0,htmltab(,https://www.google.com/))`

    Args:
        template_name (:obj:`str`): Имя шаблон

    Examples:
        >>> # Создаем шаблон с именем "New template" и получаем его guid
        >>> template = Template("New template")
        >>> template.guid
        'Y2YFAkeZ'


        >>> # Устанавливаем на шаблон минибраузер с ссылкой на google
        >>> template.content = "minibrowser(0,htmltab(,https://www.google.com/))"

        >>> # Изменяем имя шаблона на "Google search"
        >>> template.name = "Google search"

        >>> # Открываем шаблон на первом мониторе
        >>> template.show(1)
    """

    _DEFAULT_TEMPLATE = ""

    def __init__(self, template_name, host_api=host):
        self._name = template_name
        self._host_api = host_api
        self._operator_gui = BaseUtils.get_operator_gui()
        try:
            self._guid, self._template_settings = self._find_template_guid(
                template_name
            )
        except KeyError:
            self._guid, self._template_settings = self._init_template(template_name)

    def _find_template_guid(self, name):
        """Find template guid by name

        Args:
            name (str) : Template name

        Raises:
            KeyError if can't find template
        """
        templates = self._host_api.settings("templates")
        for template_ in templates.ls():
            if name == template_.name:
                return (
                    template_.guid,
                    self._host_api.settings("templates/{}".format(template_.guid)),
                )
        raise KeyError

    def _init_template(self, name):
        """Create new template

        Args:
            name (str) : Template name
        """
        self._host_api.object(self._host_api.settings("").guid + "T").create_template(
            name, self._DEFAULT_TEMPLATE
        )
        try:
            return self._find_template_guid(name)
        except KeyError:
            raise TemplateError("Failed to create template {}".format(self._name))

    @property
    def guid(self):
        """:obj:`str`: Guid шаблона"""
        return self._guid

    @guid.setter
    def guid(self, value):
        raise RuntimeError("You can't change object guid")

    @property
    def name(self):
        """:obj:`str`: Имя шаблона"""
        return self._name

    @name.setter
    def name(self, value):
        if isinstance(value, str):
            self._name = value
            self._template_settings["name"] = value
        else:
            raise TypeError("Expected str, got {}".format(type(value).__name__))

    @property
    def content(self):
        """:obj:`str`: Контент шаблона"""
        return self._template_settings["content"]

    @content.setter
    def content(self, value):
        if isinstance(value, str):
            self._template_settings["content"] = value
        else:
            raise TypeError("Expected str, got {}".format(type(value).__name__))

    def delete(self):
        """Удаляет шаблон"""
        obj = BaseUtils.get_object(self.guid)
        if obj is None:
            raise TemplateError("Template object not found!")

        obj.delete_template()

    def show(self, monitor=1):
        """Открывает шаблон на указаном мониторе

        Args:
            monitor (:obj:`int`, optional): Номер монитора. По умолчанию ``monitor=1``
        """
        self._operator_gui.show(self.guid, monitor)


class TrObject(py_object):
    """Вспомогательный класс для работы с объектами Trassir

    Attributes:
        obj (:obj:`SE_Object`): Объект trassir :obj:`object('{guid}')` или :obj:`None`
        obj_methods (List[:obj:`str`]): Список методов объекта :attr:`TrObject.obj`
        name (:obj:`str`): Имя объекта или его guid
        guid (:obj:`str`): Guid объекта
        full_guid (:obj:`str`): Полный guid :obj:`{guid объекта}_{guid сервера}`
            или :obj:`None`
        type (:obj:`str`): Тип объекта, например :obj:`"RemoteServer"`, :obj:`"Channel"`,
            :obj:`"Grabber"`, :obj:`"User"`, и др.
        path (:obj:`str`): Путь в настройках или :obj:`None`
        parent (:obj:`str`): Guid родительского объекта или :obj:`None`
        server (:obj:`str`): Guid сервера или :obj:`None`
        settings (:obj:`SE_Settings`): Объект настроек ``settings('{path}')`` или :obj:`None`

    Raises:
        TypeError: Если неправильные параметры объекта
        ValueError: Если в имени объекта есть запятые
    """

    obj, name, guid, full_guid, type = None, None, None, None, None
    path, parent, server, settings = None, None, None, None

    def __init__(self, obj, host_api=host):
        self._host_api = host_api

        if isinstance(obj, host_api.ScriptHost.SE_Settings):
            self._load_from_settings(obj)
        elif isinstance(obj, tuple):
            if len(obj) == 4:
                self._load_from_tuple(obj)
            else:
                raise TypeError(
                    "Expected tuple(name, guid, type, parent), got tuple'{}'".format(
                        obj
                    )
                )
        else:
            raise TypeError("Unexpected object type '{}'".format(type(obj).__name__))

    @staticmethod
    def _check_object_name(object_name):
        """Check if object name hasn't got commas

        Args:
            object_name (str):

        Returns:
            str: object_name.strip()

        Raises:
            ValueError: If "," found in object name
        """
        if "," in object_name:
            raise ValueError(
                "Please, rename object '{}' without commas".format(object_name)
            )
        return object_name.strip()

    @staticmethod
    def _parse_server_from_path(path):
        """Parse server guid from full path

        Args:
            path (str): Full Trassir settings path;
                example: '/pV4ggECb/_persons/n68LOBhG' returns 'pV4ggECb'
        """
        try:
            server = path.split("/", 2)[1]
        except IndexError:
            server = None

        return server

    def _find_server_guid_for_object(self, object_guid):
        """Find server guid for object

        Args:
            object_guid (str): Object guid

        Returns:
            str: Server guid if server found
            None: If server not found
        """
        all_objects = {
            obj[1]: {"name": obj[0], "guid": obj[1], "type": obj[2], "parent": obj[3]}
            for obj in self._host_api.objects_list("")
        }

        def get_parent(child_guid):
            child = all_objects.get(child_guid, None)
            if child:
                if child["type"] == "Server":
                    return child["guid"]
                else:
                    return get_parent(child["parent"])
            else:
                return None

        return get_parent(object_guid)

    def _get_object_methods(self):
        """Get object methods"""
        if self.obj:
            return [method for method in dir(self.obj) if not method.startswith("__")]
        else:
            return []

    def _load_from_settings(self, obj):
        """Preparing attributes from SE_Settings object"""
        self.obj = BaseUtils.get_object(obj.guid)
        self.obj_methods = self._get_object_methods()

        try:
            obj_name = obj.name
        except KeyError:
            obj_name = obj.guid

        self.name = self._check_object_name(obj_name)
        self.guid = obj.guid
        self.type = obj.type
        self.path = obj.path
        self.server = self._parse_server_from_path(obj.path)
        self.settings = obj

        if self.server and self.server != self.guid:
            self.full_guid = "{0.guid}_{0.server}".format(self)

    def _load_from_tuple(self, obj):
        """Preparing attributes from tuple object"""
        self.obj = BaseUtils.get_object(obj[1])
        self.obj_methods = self._get_object_methods()
        self.name = self._check_object_name(obj[0])
        self.guid = obj[1]
        self.type = obj[2]
        self.parent = obj[3]
        self.server = self._find_server_guid_for_object(obj[1])

        if self.server and self.server != self.guid:
            self.full_guid = "{0.guid}_{0.server}".format(self)

    def __repr__(self):
        return "TrObject('{}')".format(self.name)

    def __str__(self):
        return "{self.type}: {self.name} ({self.guid})".format(self=self)


class ParameterError(ScriptError):
    """Ошибка в параметрах скрипта"""

    pass


class BasicObject(py_object):
    """"""

    def __init__(self, host_api=host):
        self._host_api = host_api
        self.this_server_guid = BaseUtils.get_server_guid()

    class UniqueNameError(ScriptError):
        """Имя объекта не уникально"""

        pass

    class ObjectsNotFoundError(ScriptError):
        """Не найдены объекты с заданными именами"""

        pass

    def _check_unique_name(self, objects, object_names):
        """Check if all objects name are unique

        Args:
            objects (list): Objects list from _get_objects_from_settings

        Raises:
            UniqueNameError: If some object name is not uniques
        """
        unique_names = []
        for obj in objects:
            if obj.name in object_names:
                if obj.name not in unique_names:
                    unique_names.append(obj.name)
                else:
                    raise self.UniqueNameError(
                        "Найдено несколько объектов {obj.type} с одинаковым именем '{obj.name}'! "
                        "Задайте уникальные имена".format(obj=obj)
                    )

    @staticmethod
    def _objects_str_to_list(objects):
        """Split object names if objects is str and strip each name

        Args:
            objects (str|list): Trassir object names in comma spaced string or list

        Returns:
            list: Stripped Trassir object names

        Raises:
            ScriptError: If object name selected more than once
        """
        if isinstance(objects, str):
            objects = objects.split(",")

        names = []
        for name in objects:
            strip_name = name.strip()
            if strip_name in names:
                raise ParameterError("Объект '{}' выбран несколько раз".format(name))
            names.append(strip_name)

        return names

    def _filter_objects_by_name(self, objects, object_names):
        """Filter object by names

        Args:
            objects (list): TrObject objects list
            object_names (str|list): Trassir object names in comma spaced string or list

        Raises:
            ObjectsNotFoundError: If len(object_name) != len(filtered_object)
        """
        object_names = self._objects_str_to_list(object_names)

        self._check_unique_name(objects, object_names)

        filtered_object = [obj for obj in objects if obj.name in object_names]

        if len(filtered_object) != len(object_names):
            channels_not_found = set(object_names) - set(
                obj.name for obj in filtered_object
            )

            try:
                object_type = objects[0].type
            except IndexError:
                object_type = "Unknown"

            raise self.ObjectsNotFoundError(
                "Не найдены объекты {object_type}: {names}".format(
                    object_type=object_type,
                    names=", ".join(name for name in channels_not_found),
                )
            )

        return filtered_object


class ObjectFromSetting(BasicObject):
    """"""

    def __init__(self):
        super(ObjectFromSetting, self).__init__()

    def _load_objects_from_settings(self, settings_path, obj_type, sub_condition=None):
        """Load objects from Trassir settings

        Args:
            settings_path (:obj:`str`): Trassir settings path. Example ``"scripts"``.
                Click F4 in the Trassir settings window to show hidden parameters.
            obj_type (:obj:`str` | :obj:`list`): Loading object type. Example ``"EmailAccount"``
            sub_condition (function, optional): Function with SE_Settings as argument to filter objects

        Returns:
            list: TrObject objects list
                Example [TrObject(...), TrObject(...), ...]
        """
        try:
            settings = self._host_api.settings(settings_path)
        except KeyError:
            settings = None

        objects = []
        if settings is not None:
            if isinstance(obj_type, str):
                obj_type = [obj_type]

            if sub_condition is None:
                sub_condition = BaseUtils.do_nothing

            for obj in settings.ls():
                if obj.type in obj_type:
                    if sub_condition(obj):
                        objects.append(TrObject(obj))
        return objects

    def _get_objects_from_settings(
        self,
        settings_path,
        object_type,
        object_names=None,
        server_guid=None,
        ban_empty_result=False,
        sub_condition=None,
    ):
        """Check if objects exists and returns list from _load_objects_from_settings

        Note:
             If object_names is not None - checking if all object names are unique

        Args:
            settings_path (:obj:`str`): Trassir settings path. Example ``"scripts"``.
                Click F4 in the Trassir settings window to show hidden parameters.
            object_type (:obj:`str` | :obj:`list`): Loading object type. Example ``"EmailAccount"``
            object_names (:obj:`str` | :obj:`list`, optional): Comma spaced string or
                list of object names. Default :obj:`None`
            server_guid (:obj:`str` | :obj:`list`, optional): Server guid. Default :obj:`None`
            ban_empty_result (:obj:`bool`, optional): If True - raise error if no one object found
            sub_condition (:obj:`func`, optional) : Function with SE_Settings as argument to filter objects

        Returns:
            list: Trassir list from _load_objects_from_settings

        Raises:
            ObjectsNotFoundError: If can't find channel
        """
        if object_names == "":
            raise ParameterError("'{}' не выбраны".format(object_type))

        if server_guid is None:
            server_guid = self.this_server_guid

        if isinstance(server_guid, str):
            server_guid = [server_guid]

        objects = []

        for guid in server_guid:
            objects += self._load_objects_from_settings(
                settings_path.format(server_guid=guid), object_type, sub_condition
            )

        if ban_empty_result and not objects:
            raise self.ObjectsNotFoundError(
                "Не найдено ниодного объекта '{}'".format(object_type)
            )

        if object_names is None:
            return objects

        else:
            return self._filter_objects_by_name(objects, object_names)


class Servers(ObjectFromSetting):
    """Класс для работы с серверами

    Examples:
        >>> srvs = Servers()
        >>> local_srv = srvs.get_local()
        [TrObject('Клиент')]
        >>> # Првоерим "Здоровье" локального сервера
        >>> local_srv[0].obj.state("server_health")
        'Health Problem'
    """

    def __init__(self):
        super(Servers, self).__init__()

    def get_local(self):
        """Возвращает локальный сервер (на котором запущен скрипт)

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._load_objects_from_settings("/", ["Client", "LocalServer"])

    def get_remote(self):
        """Возвращает список удаленных серверов

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._load_objects_from_settings("/", "RemoteServer")

    def get_all(self):
        """Возвращает список всех доступных серверов

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._load_objects_from_settings(
            "/", ["Client", "LocalServer", "RemoteServer"]
        )


class Channels(ObjectFromSetting):
    """Класс для работы с каналами

    See Also:
        `Каналы - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-channels-folder.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> channels = Channels()
        >>> selected_channels = channels.get_enabled("AC-D2121IR3W 2,AC-D9141IR2 1")
        >>> selected_channels
        [TrObject('AC-D2121IR3W 2'), TrObject('AC-D9141IR2 1')]
        >>>
        >>> # Включим ручную запись на выбранных каналах
        >>> for channel in selected_channels:
        >>>     channel.obj.manual_record_start()
        >>>
        >>> # Или добавим к имени канала его guid
        >>> for channel in selected_channels:
        >>>     channel.settings["name"] += " ({})".format(channel.guid)
    """

    def __init__(self, server_guid=None):
        super(Channels, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных каналов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            not_zombie = 1 - sett["archive_zombie_flag"]
            if not_zombie:
                try:
                    return self._host_api.settings(sett.cd("info")["grabber_path"])[
                        "grabber_enabled"
                    ]
                except KeyError:
                    return 0
            return 0

        return self._get_objects_from_settings(
            "/{server_guid}/channels",
            "Channel",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных каналов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            zombie = sett["archive_zombie_flag"]
            if not zombie:
                try:
                    return (
                        1
                        - self._host_api.settings(sett.cd("info")["grabber_path"])[
                            "grabber_enabled"
                        ]
                    )
                except KeyError:
                    return 1
            return 1

        return self._get_objects_from_settings(
            "/{server_guid}/channels",
            "Channel",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех каналов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/channels",
            "Channel",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Devices(ObjectFromSetting):
    """Класс для работы с ip устройствами

    See Also:
        `IP-устройства - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-ip-cameras-folder.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> devices = Devices()
        >>> enabled_devices = devices.get_enabled()
        >>> enabled_devices
        [TrObject('AC-D2121IR3W'), TrObject('AC-D5123IR32'), ...]
        >>>
        >>> # Перезагрузим все устройства
        >>> for dev in enabled_devices:
        >>>     dev.settings["reboot"] = 1
    """

    def __init__(self, server_guid=None):
        super(Devices, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных устройств

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["grabber_enabled"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/ip_cameras",
            "Grabber",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных устройств

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["grabber_enabled"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/ip_cameras",
            "Grabber",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех устройств

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/ip_cameras",
            "Grabber",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Scripts(ObjectFromSetting):
    """Класс для работы со скриптами

    See Also:
        `Скрипты - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-script-feature.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> scripts = Scripts()
        >>> all_scripts = scripts.get_all()
        >>> all_scripts
        [TrObject('Новый скрипт'), TrObject('HDD Health Monitor'), TrObject('Password Reminder')]
        >>>
        >>> # Отключим все скрипты
        >>> for script in all_scripts:
        >>>     script.settings["enable"] = 0
    """

    def __init__(self, server_guid=None):
        super(Scripts, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных скриптов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["enable"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Script",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных скриптов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["enable"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Script",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех скриптов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Script",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Rules(ObjectFromSetting):
    """Класс для работы с правилами

    See Also:
        `Правила - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-rule.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> rules = Rules()
        >>> all_rules = rules.get_all()
        >>> all_rules
        [TrObject('!Rule'), TrObject('NEW RULE'), TrObject('Новое правило')]
        >>>
        >>> # Отключим все правила
        >>> for rule in all_rules:
        >>>     rule.settings["enable"] = 0
    """

    def __init__(self, server_guid=None):
        super(Rules, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных правил

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["enable"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Rule",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных правил

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["enable"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Rule",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех правил

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен. По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Rule",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Schedules(ObjectFromSetting):
    """Класс для работы с расписаниями

    See Also:
        `Расписания - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-schedule.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> schedules = Schedules()
        >>> my_schedule = schedules.get_enabled("!Schedule")[0]
        >>> my_schedule.obj.state("color")
        'Red'
    """

    def __init__(self, server_guid=None):
        super(Schedules, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных расписаний

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["enable"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Schedule",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных расписаний

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["enable"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Schedule",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех расписаний

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "Schedule",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class TemplateLoops(ObjectFromSetting):
    """Класс для работы с циклическими просмотрами шаблонов

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> tmplate_loops = TemplateLoops()
        >>> tmplate_loops.get_all()
        [TrObject('Новый циклический просмотр')]
    """

    def __init__(self, server_guid=None):
        super(TemplateLoops, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных циклических просмотров шаблонов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["enable"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "TemplateLoop",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных циклических просмотров шаблонов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["enable"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "TemplateLoop",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех циклических просмотров шаблонов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "TemplateLoop",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class EmailAccounts(ObjectFromSetting):
    """Класс для работы с E-Mail аккаунтами

    See Also:
        `Добавление учетной записи e-mail - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-email-account.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> email_accounts = EmailAccounts()
        >>> email_accounts.get_all()
        [TrObject('Новая учетная запись e-mail'), TrObject('MyAccount')]
    """

    def __init__(self, server_guid=None):
        super(EmailAccounts, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_all(self, names=None):
        """Возвращает список всех E-Mail аккаунтов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/scripts",
            "EmailAccount",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class NetworkNodes(ObjectFromSetting):
    """Класс для работы с сетевыми подключениями

    See Also:
        `Сеть - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-network-folder.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> network_nodes = NetworkNodes("client")
        >>> network_nodes.get_enabled()
        [TrObject('QuattroStationPro (172.20.0.101)'), TrObject('NSK-HD-01 (127.0.0.1)')]
    """

    def __init__(self, server_guid=None):
        super(NetworkNodes, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных сетевых подключений

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["should_be_connected"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/network",
            "NetworkNode",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных сетевых подключений

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["should_be_connected"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/network",
            "NetworkNode",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех сетевых подключений

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/network",
            "NetworkNode",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class PosTerminals(ObjectFromSetting):
    """Класс для работы с POS Терминалами

    See Also:
        `Настройка POS-терминалов - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-pos-terminals-folder.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> pos_terminals = PosTerminals()
        >>> pos_terminals.get_disabled()
        [TrObject('Касса (1)')]
    """

    def __init__(self, server_guid=None):
        super(PosTerminals, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_enabled(self, names=None):
        """Возвращает список активных POS Терминалов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return sett["pos_enable"]
            except KeyError:
                return 0

        return self._get_objects_from_settings(
            "/{server_guid}/pos_folder2/terminals",
            "PosTerminal",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_disabled(self, names=None):
        """Возвращает список неактивных POS Терминалов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        def sub_condition(sett):
            try:
                return 1 - sett["pos_enable"]
            except KeyError:
                return 1

        return self._get_objects_from_settings(
            "/{server_guid}/pos_folder2/terminals",
            "PosTerminal",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )

    def get_all(self, names=None):
        """Возвращает список всех POS Терминалов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        return self._get_objects_from_settings(
            "/{server_guid}/pos_folder2/terminals",
            "PosTerminal",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Users(ObjectFromSetting):
    """Класс для работы с пользователями и их группами.

    See Also:
        `Пользователи - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-users-folder.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> users = Users()
        >>> users.get_groups()
        [TrObject('TEST')]
    """

    def __init__(self, server_guid=None):
        super(Users, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_groups(self, names=None):
        """Возвращает список групп пользователей

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_settings(
            "/{server_guid}/users",
            "Group",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_users(self, names=None):
        """Возвращает список пользователей

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_settings(
            "/{server_guid}/users",
            "User",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_users_by_groups(self, group_names):
        """Возвращает список пользователей из указанных групп

        Args:
            group_names (:obj:`str` | :obj:`list`): :obj:`str` - имена групп,
                разделенные запятыми или :obj:`list` - список имен.

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        if group_names is None:
            groups = [""]
        else:
            groups = [group.guid for group in self.get_groups(names=group_names)]

        def sub_condition(sett):
            return sett["group"] in groups

        return self._get_objects_from_settings(
            "/{server_guid}/users",
            "User",
            object_names=None,
            server_guid=self.server_guid,
            sub_condition=sub_condition,
        )


class Templates(ObjectFromSetting):
    """Класс для работы с существующими шаблонами.

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> templates = Templates(BaseUtils.get_server_guid())
        >>> templates.get_all()
        [TrObject('Parking'), TrObject('FR'), TrObject('AT'), TrObject('AD+')]
    """

    def __init__(self, server_guid=None):
        super(Templates, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_all(self, names=None):
        """Возвращает список шаблонов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_settings(
            "/{server_guid}/templates",
            "Template",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Persons(ObjectFromSetting):
    """Класс для работы с персонами и их папками.

    See Also:
        `Персоны - Руководство пользователя Trassir
        <https://www.dssl.ru/files/trassir/manual/ru/setup-persons-folder.html>`_

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
            >>> persons = Persons()
            >>> persons.get_folders()
            [TrObject('Мошенники'), TrObject('DSSL'), TrObject('persons')]
            >>> persons.get_persons()
            [
                {
                    'name': 'Leonardo',
                    'guid': 'cJuJYAha',
                    'gender': 0,
                    'birth_date': '1980-01-01',
                    'comment': 'Comment',
                    'contact_info': 'Contact info',
                    'folder_guid': 'n68LOBhG',
                    'image': <image, str>,
                    'image_guid': 'gBHZ2vpz',
                    'effective_rights': 0,
                },
                ...
            ]
            >>> persons.get_person_by_guid("cJuJYAha")
            {
                'name': 'Leonardo',
                'guid': 'cJuJYAha',
                'gender': 0,
                'birth_date': '1980-01-01',
                'comment': 'Comment',
                'contact_info': 'Contact info',
                'folder_guid': 'n68LOBhG',
                'image': <image, str>,
                'image_guid': 'gBHZ2vpz',
                'effective_rights': 0,
            }
    """

    _PERSONS_UPDATE_TIMEOUT = 10 * 60  # Time in sec between update _persons dict

    def __init__(self, server_guid=None):
        super(Persons, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        if isinstance(server_guid, str):
            server_guid = [server_guid]

        self.server_guid = server_guid

        self._persons = None

    def _update_persons_dict(self, timeout=10):
        """Updating self._persons dict"""
        persons = self.get_persons(timeout=timeout)
        by_guid, by_name = {}, {}
        for person in persons:
            by_guid[person["guid"]] = person
            by_name[person["name"]] = person

        self._persons = {
            "update_ts": int(time.time()),
            "by_guid": by_guid,
            "by_name": by_name,
        }

    def _check_loaded_persons(self, timeout=10):
        """This method check if self._persons dict is need to be updated"""
        ts_now = int(time.time())

        if (
            self._persons is None
            or (ts_now - self._persons["update_ts"]) > self._PERSONS_UPDATE_TIMEOUT
        ):
            self._update_persons_dict(timeout=timeout)

    def get_folders(self, names=None):
        """Возвращает список папок персон

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        try:
            folders = self._get_objects_from_settings(
                "/{server_guid}/persons",
                "PersonsSubFolder",
                object_names=names,
                server_guid=self.server_guid,
            )

            if names is None or "persons" in names:
                for guid in self.server_guid:
                    try:
                        settings = self._host_api.settings("/{}/persons".format(guid))
                    except KeyError:
                        continue

                    folders.append(TrObject(settings))

        except self.ObjectsNotFoundError as err:
            folders = []
            names = self._objects_str_to_list(names)

            if names is None or "persons" in names:
                for guid in self.server_guid:
                    try:
                        settings = self._host_api.settings("/{}/persons".format(guid))
                    except KeyError:
                        continue

                    folders.append(TrObject(settings))

            if not folders:
                raise err

        return folders

    def get_persons(self, folder_names=None, timeout=10):
        """Возвращает список персон

        Note:
            Данный метод работает только с локальной БД.

        Args:
            folder_names (:obj:`str` | List[:obj:`str`], optional): :obj:`str` -
                названия папок персон, разделенные запятыми или :obj:`list` -
                список папок персон. По умолчанию :obj:`None`
            timeout (:obj:`int`, optional): Макс. время запроса к БД.
                По умолчанию ``timeout=10``

        Returns:
            List[:obj:`dict`]: Список персон - если персоны найдены

        Raises:
            EnvironmentError: Если произошла ошибка при запросе в БД.
            TrassirError: Если в данной сборке Trassir нет метода :obj:`host.service_persons_get`
        """
        tmp_server_guid = self.server_guid[:]
        self.server_guid = [self.this_server_guid]
        persons_folders = self.get_folders(names=folder_names)
        self.server_guid = tmp_server_guid[:]

        try:
            persons = self._host_api.service_persons_get(
                [folder.guid for folder in persons_folders], True, 0, 0, timeout
            )
        except AttributeError:
            raise TrassirError(
                "Данный функционал не поддерживается вашей сборкой Trassir. "
                "Попробуйте обновить ПО."
            )

        if isinstance(persons, str):
            raise EnvironmentError(persons)

        return persons

    def get_person_by_guid(self, person_guid, timeout=10):
        """Возвращает информацию о персоне по его guid

        Note:
            Для уменьшения кол-ва запросов к БД - метод создает локальную
            копию всех персон при первом запросе и обновляет ее вместе
            с последующими запросами не чаще чем 1 раз в 10 минут.

        Args:
            person_guid (:obj:`str`): Guid персоны
            timeout (:obj:`int`, optional): Макс. время запроса к БД.
                По умолчанию ``timeout=10``

        Returns:
            :obj:`dict`: Даные о персоне или :obj:`None` если персона не найдена
        """
        self._check_loaded_persons(timeout=timeout)
        return self._persons["by_guid"].get(person_guid)

    def get_person_by_name(self, person_name, timeout=10):
        """Возвращает информацию о персоне по его имени

        Note:
            Для уменьшения кол-ва запросов к БД - метод создает локальную
            копию всех персон при первом запросе и обновляет ее вместе
            с последующими запросами не чаще чем 1 раз в 10 минут.

        Args:
            person_name (:obj:`str`): Имя персоны
            timeout (:obj:`int`, optional): Макс. время запроса к БД.
                По умолчанию ``timeout=10``

        Returns:
            :obj:`dict`: Даные о персоне или :obj:`None` если персона не найдена
        """
        self._check_loaded_persons(timeout=timeout)
        return self._persons["by_name"].get(person_name)


class ObjectFromList(BasicObject):
    """"""

    def __init__(self):
        super(ObjectFromList, self).__init__()

    def _load_objects_from_list(self, obj_type, sub_condition=None):
        """Load objects from Trassir objects_list method

        Args:
            obj_type (str | list): Loading object type; example: "EmailAccount"
            sub_condition (function, optional): Function with SE_Settings as argument to filter objects

        Returns:
            list: TrObject objects list
                Example [TrObject(...), TrObject(...), ...]
        """
        if sub_condition is None:
            sub_condition = BaseUtils.do_nothing

        objects = []
        for obj in self._host_api.objects_list(obj_type):
            if sub_condition(obj):
                objects.append(TrObject(obj))

        return objects

    def _get_objects_from_list(
        self,
        object_type,
        object_names=None,
        server_guid=None,
        ban_empty_result=False,
        sub_condition=None,
    ):
        """Check if objects exists and returns list from _load_objects_from_settings

        Note:
             If object_names is not None - checking if all object names are unique

        Args:
            object_type (str|list): Loading object type; example: "EmailAccount"
            object_names (str|list, optional): Comma spaced string or list of object names; default: None
            server_guid (str|list, optional): Server guids; default: None
            ban_empty_result (bool, optional): If True - raise ObjectsNotFoundError if no one object found
            sub_condition (func, optional) : Function with SE_Settings as argument to filter objects

        Returns:
            list: Trassir list from _load_objects_from_settings

        Raises:
            ObjectsNotFoundError: If can't find channel
        """
        if object_names == "":
            raise ParameterError("'{}' не выбраны".format(object_type))

        if server_guid is None:
            server_guid = self.this_server_guid
        else:
            if isinstance(server_guid, str):
                server_guid = [server_guid]

        objects = self._load_objects_from_list(object_type, sub_condition)

        objects = [obj for obj in objects if obj.server in server_guid]

        if ban_empty_result and not objects:
            raise self.ObjectsNotFoundError(
                "Не найдено ниодного объекта '{}'".format(object_type)
            )

        if object_names is None:
            return objects

        else:
            return self._filter_objects_by_name(objects, object_names)

    def _zone_type(self, zone_obj):
        """Возвращает тип зоны для объекта

        Args:
            zone_obj (:obj:`SE_Object`): Объект trassir ``object('{guid}')``

        Returns:
            :obj:`str`: Тип объекта
            :obj:`None`: Если тип зоны неизвестен
        """

        if not isinstance(zone_obj, self._host_api.ScriptHost.SE_Object):
            raise TypeError(
                "Expected SE_Object, got '{}'".format(type(zone_obj).__name__)
            )

        try:
            guid = zone_obj.guid
            channel, server = zone_obj.associated_channel.split("_")
        except (AttributeError, ValueError):
            return None

        try:
            zones_dir = self._host_api.settings(
                "/{}/channels/{}/people_zones".format(server, channel)
            )
            for i in xrange(16):
                if zones_dir["zone%02d_guid" % i] == guid:
                    func_type = zones_dir["zone%02d_func_type" % i]
                    if isinstance(func_type, int):
                        return (
                            ["Queue", "Workplace"][func_type]
                            if func_type in range(2)
                            else "Queue"
                        )
                    else:
                        return func_type
        except KeyError:
            "not a queue or workplace"

        try:
            zones_dir = self._host_api.settings(
                "/{}/channels/{}/workplace_zones".format(server, channel)
            )
            for i in xrange(16):
                if zones_dir["zone%02d_guid" % i] == guid:
                    return "Workplace"
        except KeyError:
            "not a workplace"

        try:
            zones_dir = settings("/%s/channels/%s/deep_people" % (server, channel))
            for i in xrange(16):
                if zones_dir["zone%02d_guid" % i] == guid:
                    if zones_dir["zone%02d_type" % i] in ["border", "border_swapped"]:
                        return "Border"
                    else:
                        return "Queue"
        except KeyError:
            "not a deep people queue"


class GPIO(ObjectFromList):
    """Класс для работы с тревожными входами/выходами

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> gpio = GPIO()
        >>> gpio_door = gpio.get_inputs("Door")[0]
        >>> gpio_door.obj.state("gpio_input_level")
        'Input Low (Normal High)'
        >>> gpio_light = gpio.get_outputs("Light")[0]
        >>> gpio_light.obj.set_output_high()
    """

    def __init__(self, server_guid=None):
        super(GPIO, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_inputs(self, names=None):
        """Возвращает список тревожных входов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "GPIO Input",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_outputs(self, names=None):
        """Возвращает список тревожных выходов

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "GPIO Output",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Zones(ObjectFromList):
    """Класс для работы с зонами

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> zones = Zones()
        >>> zones.get_queues("Касса 1")[0].obj.state("zone_queue")
        '5+'
    """

    def __init__(self, server_guid=None):
        super(Zones, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_people(self, names=None):
        """Возвращает список PeopleZones

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "PeopleZone",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_simt(self, names=None):
        """Возвращает список зон SIMT

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "SIMT Zone",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_workplaces(self, names=None):
        """Возвращает список рабочих зон

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        people_zones = self.get_people(names=names)

        return [
            zone
            for zone in people_zones
            if self._zone_type(zone.obj) in ["Workplace", "Рабочее место"]
        ]

    def get_queues(self, names=None):
        """Возвращает список зон очередей

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        people_zones = self.get_people(names=names)

        return [
            zone
            for zone in people_zones
            if self._zone_type(zone.obj) in ["", "Queue", "Очередь"]
        ]

    def get_shelves(self, names=None):
        """Возвращает список зон полок

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "Shelf",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class Borders(ObjectFromList):
    """Класс для работы с линиями пересечения

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.

    Examples:
        >>> borders = Borders()
        >>> borders.get_simt()
        [TrObject('DBOP')]
        >>> borders.get_all()
        [TrObject('Вход в офис'), TrObject('DBOP')]
    """

    def __init__(self, server_guid=None):
        super(Borders, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_head(self, names=None):
        """Возвращает список HeadBorders

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "HeadBorder",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_people(self, names=None):
        """Возвращает список PeopleBorders

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "PeopleBorder",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_simt(self, names=None):
        """Возвращает список SIMT Borders

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "SIMT Border",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

    def get_deep_people(self, names=None):
        """Возвращает список DeepPeopleBorders

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        people_zones = self._get_objects_from_list(
            "PeopleZone",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )

        return [zone for zone in people_zones if self._zone_type(zone.obj) == "Border"]

    def get_all(self, names=None):
        """Возвращает список всех линий пересечения

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """
        all_borders = (
            self.get_head()
            + self.get_people()
            + self.get_simt()
            + self.get_deep_people()
        )

        if names is None:
            return all_borders
        else:
            return self._filter_objects_by_name(all_borders, names)


class Sigur(ObjectFromList):
    """Класс для работы со СКУД Sigur

    Args:
        server_guid (:obj:`str` | List[:obj:`str`], optional): Guid сервера или список guid.
            По умолчанию :obj:`None`, что соотвествует всем доступным серверам.
    """

    def __init__(self, server_guid=None):
        super(Sigur, self).__init__()
        if server_guid is None:
            server_guid = [srv.guid for srv in Servers().get_all()]

        self.server_guid = server_guid

    def get_access_points(self, names=None):
        """Возвращает список точек доступа

        Args:
            names (:obj:`str` | :obj:`list`, optional): :obj:`str` - имена,
                разделенные запятыми или :obj:`list` - список имен.
                По умолчанию :obj:`None`

        Returns:
            List[:class:`TrObject`]: Список объектов
        """

        return self._get_objects_from_list(
            "Access Point",
            object_names=names,
            server_guid=self.server_guid,
            sub_condition=None,
        )


class TrassirError(ScriptError):
    """Exception if bad trassir version"""

    pass


class PokaYoke(py_object):
    """Класс для защиты от дурака

    Позволяет блокировать запуск скрипта на ПО, где это
    не предусмотрено (например, на клиенте или TOS).
    А также производить некоторые другие проверки.
    """

    _EMAIL_REGEXP = re.compile(
        r"[^@]+@[^@]+\.[^@]+"
    )  # Default regex to check emails list
    _PHONE_REGEXP = re.compile(r"[^\d,;]")  # Default regex to check phone list

    _host_api = host

    def __init__(self):
        pass

    @staticmethod
    def ban_tos():
        """Блокирует запуск скрипта на `Trassir OS`

        Raises:
            OSError: Если скрипт запускается на `Trassir OS`

        Examples:
            >>> PokaYoke.ban_tos()
            OSError: Скрипт недоступен для TrassirOS
        """
        if os.name != "nt":
            raise OSError("Скрипт недоступен для TrassirOS")

    @staticmethod
    def ban_win():
        """Блокирует запуск скрипта на `Windows OS`

        Raises:
            OSError: Если скрипт запускается на `Windows OS`

        Examples:
            >>> PokaYoke.ban_win()
            OSError: Скрипт недоступен для WindowsOS
        """
        if os.name == "nt":
            raise OSError("Скрипт недоступен для WindowsOS")

    @staticmethod
    def ban_client():
        """Блокирует запуск скрипта на `Trassir Client`

        Raises:
            TrassirError: Если скрипт запускается на `Trassir Client`

        Examples:
            >>> PokaYoke.ban_client()
            TrassirError: Скрипт недоступен для клиентской версии Trassir
        """
        if BaseUtils.get_server_guid() == "client":
            raise TrassirError("Скрипт недоступен для клиентской версии Trassir")

    @classmethod
    def ban_daemon(cls):
        """Блокирует запуск скрипта на сервре Trassir, который запущен как служба

        Raises:
            TrassirError: Если скрипт запускается на сервре Trassir,
                который запущен как служба

        Examples:
            >>> PokaYoke.ban_daemon()
            TrassirError: Скрипт недоступен для Trassir запущенным как служба
        """
        if cls._host_api.settings("system_wide_options")["daemon"]:
            raise TrassirError("Скрипт недоступен для Trassir запущенным как служба")

    @staticmethod
    def check_email_account(account_name):
        """Проверяет существование E-Mail аккаунта

        Args:
            account_name (:obj:`str`): Имя E-Mail аккаунта

        Returns:
             List[:class:`TrObject`]: Список объектов

        Raises:
            ParameterError: Если аккаунт не выбран
            ObjectsNotFoundError: Если аккаунт не найден

        Examples:
            >>> PokaYoke.check_email_account("")
            ParameterError: 'EmailAccount' не выбраны
            >>> PokaYoke.check_email_account("YourAccount")
            ObjectsNotFoundError: Не найдены объекты EmailAccount: YourAccount
            >>> PokaYoke.check_email_account("MyAccount")
            [TrObject('MyAccount')]
        """
        e_accounts = EmailAccounts(BaseUtils.get_server_guid())
        return e_accounts.get_all(account_name)

    @classmethod
    def parse_emails(cls, mailing_list, regex=None):
        """Парсит email дреса из строки

        Каждый email проверяется с помощью regex ``r"[^@]+@[^@]+\.[^@]+"``.

        Args:
            mailing_list (:obj:`str`): Список email адресов, разделенный запятыми
            regex (:obj:`SRE_Pattern`, optional): Новый regex шаблон для проверки.
                По умолчанию :obj:`None`

        Returns:
            List[:obj:`str`]: Список адресов

        Raises:
            ParameterError: Если найден невалидный email

        Examples:
            >>> PokaYoke.parse_emails("a.trubilil!dssl.ru,support@dssl.ru")
            ParameterError: Email 'a.trubilil!dssl.ru' is not valid!
            >>>
            >>> PokaYoke.parse_emails("a.trubilil@dssl.ru,support@dssl.ru")
            ['a.trubilil@dssl.ru', 'support@dssl.ru']
        """
        mailing_list = mailing_list.replace(" ", "")

        if not mailing_list:
            raise ParameterError("No emails to send!")

        if regex is None:
            regex = cls._EMAIL_REGEXP
        else:
            if not isinstance(regex, cls._EMAIL_REGEXP.__class__):
                raise TypeError(
                    "Expected re.compile, got '{}'".format(type(regex).__name__)
                )

        if isinstance(mailing_list, str):
            mailing_list = mailing_list.split(",")

        mailing_list = [mail.strip() for mail in mailing_list]

        for mail in mailing_list:
            if not regex.match(mail):
                raise ParameterError("Email '{}' is not valid!".format(mail))

        return mailing_list

    @classmethod
    def check_phones(cls, phones, regex=None):
        """Проверяет строку на валидность телефонных номеров

        Строка проверяется с помощью regex ``r"[^\d,;]"``.

        Args:
            phones (:obj:`str`): Список телефонов, разделенный запятыми или точкой с запятой
            regex (:obj:`SRE_Pattern`, optional): Новый regex шаблон для проверки.
                По умолчанию :obj:`None`

        Returns:
            :obj:`str`: Список номеров телефона

        Raises:
            ParameterError: Если найден невалидный номер телефона

        Examples:
            >>> PokaYoke.check_phones("79999999999,78888888888A")
            ParameterError: Bad chars in phone list: `A`
            >>>
            >>> PokaYoke.check_phones("a.trubilil@dssl.ru,support@dssl.ru")
            '79999999999,78888888888'
        """
        phones = phones.replace(" ", "")

        if not phones:
            raise ParameterError("No phones!")

        if regex is None:
            regex = cls._PHONE_REGEXP
        else:
            if not isinstance(regex, cls._PHONE_REGEXP.__class__):
                raise TypeError(
                    "Expected re.compile, got '{}'".format(type(regex).__name__)
                )
        bad_chars = regex.findall(phones)
        if bad_chars:
            raise ParameterError(
                "Bad chars in phone list: `{}`".format(", ".join(bad_chars))
            )

        return phones


class SenderError(Exception):
    """Base Sender Exception"""

    pass


class Sender(py_object):
    _HTML_IMG_TEMPLATE = """<img src="data:image/png;base64,{img}" {attr}>"""

    def __init__(self, host_api=host):
        self._host_api = host_api

    @staticmethod
    def _get_base64(image_path):
        """Returns base64 image

        Args:
            image_path (str): Image full path
        """
        image_path = BaseUtils.win_encode_path(image_path)
        if os.path.isfile(image_path):
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read())

    @staticmethod
    def _get_html_img(image_base64, **kwargs):
        """Returns html img

        Args:
            image_base64 (str): Base64 image
        """
        return BaseUtils.base64_to_html_img(image_base64, **kwargs)

    def text(self, text):
        """Send text

        Args:
            text (str): Text message
        """
        pass

    def image(self, image_path, text=""):
        """Send image and optional text

        Args:
            image_path (str): Image path
            text (str, optional): Text message; default: ""
        """
        pass

    def files(self, file_paths, text=""):
        """Send file or list of files

        Args:
            file_paths (str|list): File path or list of paths
            text (str, optional): Text message; default: ""
        """
        pass


class PopupSender(Sender):
    """Класс для показа всплывающих окон в правом нижнем углу экрана

    Args:
        width (:obj:`int`, optional): Ширина изображения, px.
            По умолчанию :obj:`width=400`

    Examples:
        >>> sender = PopupSender(300)
        >>> sender.text("Hello World!")

            .. image:: images/popup_sender.text.png

        >>> sender.image(r"manual\en\cloud-devices-16.png")

            .. image:: images/popup_sender.image.png
    """

    def __init__(self, width=400):
        super(PopupSender, self).__init__()
        self._attr = {"width": width}

    def text(self, text, popup_type="message"):
        """Показывает текст во всплывающем окне

        Вызывает один из методов Trassir :obj:`host.alert`,
        :obj:`host.message` или :obj:`host.error` с текстом

        Args:
            text (:obj:`str`): Текст сообщения
            popup_type (:obj:`"message"` | :obj:`"alert"` | :obj:`"error"`, optional)
                Тип сообщения. По умолчанию :obj:`"message"`
        """

        if popup_type == "alert":
            self._host_api.alert(text)
        elif popup_type == "error":
            self._host_api.error(text)
        else:
            self._host_api.message(text)

    def image(self, image_path, text="", popup_type=None):
        """Показывает изображение во всплывающем окне

        Args:
            image_path (:obj:`str`): Полный путь до изображения
            text (:obj:`str`, optional): Текст сообщения. По умолчанию :obj:`""`
            popup_type (:obj:`"message"` | :obj:`"alert"` | :obj:`"error"`, optional)
                Тип сообщения. По умолчанию :obj:`"message"`
        """
        image_base64 = self._get_base64(image_path)

        if not image_base64:
            self.text("<b>File not found</b><br>{}".format(image_path), popup_type)
            return

        html_image = BaseUtils.base64_to_html_img(image_base64, **self._attr)

        html = "{image}"
        if text:
            html = "<b>{text}</b><br>{image}"

        self.text(html.format(text=text, image=html_image), popup_type)


class PopupWithBtnSender(Sender):
    """Класс для показа всплывающих окон с кнопкой `Оk`

    Note:
        | Для закрытия окна необходимо нажать кнопку `Ok` в течении 60 сек.
        | После 60 сек окно закрывается автоматически.

    Args:
        width (:obj:`int`, optional): Ширина изображения, px.
            По умолчанию :obj:`width=800`

    Examples:
        >>> sender = PopupWithBtnSender()
        >>> sender.text("Hello World!")

            .. image:: images/popup_with_btn_sender.text.png

        >>> sender.image(r"manual\en\cloud-devices-16.png")

            .. image:: images/popup_with_btn_sender.image.png
    """

    def __init__(self, width=800):
        super(PopupWithBtnSender, self).__init__()
        self._attr = {"width": width}

    def text(self, text):
        """Показывает текст во всплывающем окне

        Вызывает метод Trassir :obj:`host.question` с текстом

        Args:
            text (:obj:`str`): Текст сообщения
        """
        self._host_api.question(
            "<pre>{}</pre>".format(text), "Ok", BaseUtils.do_nothing
        )

    def image(self, image_path, text=""):
        """Показывает изображение во всплывающем окне

        Args:
            image_path (:obj:`str`): Полный путь до изображения
            text (:obj:`str`, optional): Текст сообщения. По умолчанию :obj:`""`
        """
        image_base64 = self._get_base64(image_path)

        if not image_base64:
            self.text("<b>File not found</b><br>{}".format(image_path))
            return

        html_image = BaseUtils.base64_to_html_img(image_base64, **self._attr)

        html = "{image}"
        if text:
            html = "<b>{text}</b><br>{image}"

        self.text(html.format(text=text, image=html_image))


class EmailSender(Sender):
    """Класс для отправки уведомлений, изображений и файлоа на почту

    Note:
        По умолчанию тема сообщений соответствует шаблону
        ``{server_name} -> {script_name}``

    Tip:
        При отправке изображения с текстом предпочтительней использовать метод
        :meth:`EmailSender.image` с необязательным аргументом :obj:`text` чем
        :meth:`EmailSender.text` с необазательным аргументом :obj:`attachments`

    Args:
        account (:obj:`str`): E-Mail аккаунт trassir. Проверяется
            методом :meth:`PokaYoke.check_email_account`
        mailing_list (:obj:`str`): Список email адресов для отправки писем
            разделенный запятыми. Проверяется и парсится в список методом
            :meth:`PokaYoke.parse_emails`
        subject (:obj:`str`, optional): Общая тема для сообщений.
            По умолчанию :obj:`None`
        max_size (:obj:`int`, optional): Максимальный размер вложения, байт.
            По умолчанию 25 * 1024 * 1024

    Examples:
        >>> sender = EmailSender("MyAccount", "my_mail@google.com")
        >>> sender.text("Hello World!")

            .. image:: images/email_sender.text.png

        >>> sender.image(r"manual\en\cloud-devices-16.png")

            .. image:: images/email_sender.image.png

        >>> sender.files([r"manual\en\cloud.html", r"manual\en\cloud.png"])

            .. image:: images/email_sender.files.png
    """

    def __init__(self, account, mailing_list, subject=None, max_size=None):
        super(EmailSender, self).__init__()

        PokaYoke.check_email_account(account)

        self.max_size = max_size or 25 * 1024 * 1024

        self._account = account
        self._mailing_list = PokaYoke.parse_emails(mailing_list)

        self._subject_default = subject or self._generate_subject()

    def _generate_subject(self):
        """Returns `server name` -> `script name`"""
        subject = "{server_name} -> {script_name}".format(
            server_name=self._host_api.settings("").name,
            script_name=self._host_api.stats().parent()["name"],
        )
        return subject

    def _group_files_by_max_size(self, file_paths, max_size):
        """Split files to groups. Size of each group is less then max_size

        Args:
            file_paths (list): List of files
            max_size (int): Max group size, bytes
        """
        group = []
        cur_size = 0
        for idx, file_path in enumerate(file_paths):
            file_size = os.stat(BaseUtils.win_encode_path(file_path)).st_size
            if not cur_size or (cur_size + file_size) < max_size:
                cur_size += file_size
                group.append(file_path)
            else:
                break
        else:
            return [group]

        return [group] + self._group_files_by_max_size(file_paths[idx:], max_size)

    def text(self, text, subject=None, attachments=None):
        """Отправка текстового сообщения

        Args:
            text (:obj:`str`): Текст сообщения
            subject (:obj:`str`, optional): Новая тема сообщения.
                По умолчанию :obj:`None`
            attachments (:obj:`list`, optional): Список вложений.
                По умолчанию :obj:`None`
        """
        if attachments is None:
            attachments = []
        self._host_api.send_mail_from_account(
            self._account,
            self._mailing_list,
            subject or self._subject_default,
            text,
            attachments,
        )

    def image(self, image_path, text="", subject=None):
        """Отправка изображения

        Args:
            image_path (:obj:`str`): Полный путь до изображения
            text (:obj:`str`, optional): Текст сообщения.
                По умолчанию :obj:`""`
            subject (:obj:`str`, optional): Новая тема сообщения.
                По умолчанию :obj:`None`
        """
        self.files([image_path], text=text, subject=subject)

    def files(self, file_paths, text="", subject=None, callback=None):
        """Отправка файлов

        Note:
            Если отправляется несколько файлов они могут быть разделены на
            несколько сообщений, основываясь на максимальном размере вложений.

        Args:
            file_paths (:obj:`str` | :obj:`list`): Путь до файла или список
                файлов для отправки
            text (:obj:`str`, optional): Текст сообщения.
                По умолчанию :obj:`""`
            subject (:obj:`str`, optional): Новая тема сообщения.
                По умолчанию :obj:`None`
            callback (:obj:`function`, optional): Функция, которая вызывается после
                отправки частей
        """
        if isinstance(file_paths, str):
            file_paths = [file_paths]

        if callback is None:
            callback = BaseUtils.do_nothing

        files_to_send = []
        for path in file_paths:
            if os.path.isfile(BaseUtils.win_encode_path(path)):
                files_to_send.append(path)
            else:
                text += "\nFile not found: {}".format(path)

        file_groups = self._group_files_by_max_size(files_to_send, self.max_size)

        for grouped_files in file_groups:
            self.text(text, subject=subject, attachments=grouped_files)
            callback(grouped_files)


class SMSCSenderError(SenderError):
    """Raises with SMSCSender errors"""
    pass

class SMSCSender(Sender):
    """Класс для отправки сообщений с помощью сервиса smsc.ru

    See Also:
        `https://smsc.ru/api/http/ <https://smsc.ru/api/http/>`_

    Note:
        | Номера проверяются методом
          :meth:`PokaYoke.check_phones`
        | Также при первом запуске скрипт проверяет данные авторизации

    Warnings:
        | По умолчанию сервис smsc.ru отправляет сообщения от своего имени *SMSC.RU.*
          При этом отправка на номера Мегафон/Йота **недоступна** т.к. имя *SMSC.RU*
          заблокировано оператором.
        |
        | Мы настоятельно **НЕ** рекомендуем использовать стандартное имя *SMSC.RU.*
        |
        | Для отправки смс от вашего буквенного имени необходимо его
          создать в разделе - https://smsc.ru/senders/ и зарегистрировать для
          операторов в колонке Действия по кнопке Изменить (после заключения договора
          согласно инструкции - https://smsc.ru/contract/info/ ) а также приложить
          гарантийное письмо на МТС в личный кабинет http://smsc.ru/documents/ и
          отправить на почту inna@smsc.ru

    Args:
        login (:obj:`str`): SMSC Логин
        password (:obj:`str`): SMSC Пароль
        phones (:obj:`str`): Список номеров для отправки смс резделенный
            запятыми или точкой с запятой
        translit(:obj:`bool`, optional): Переводить сообщение в
            транслит. По умолчанию :obj:`True`

    Raises:
        SMSCSenderError: При любых ошибках с отправкой сообщения

    Examples:
        >>> sender = SMSCSender("login", "password", "79999999999")
        >>> sender.text("Hello World!")

            .. image:: images/smsc_sender.text.png
    """

    _BASE_URL = "https://smsc.ru/sys/send.php?{params}"
    _ERROR_CODES = {
        1: "URL Params error",
        2: "Invalid login or password",
        3: "Not enough money",
        4: "Your IP is temporary blocked. More info: https://smsc.ru/faq/99",
        5: "Bad date format",
        6: "Message is denied (by text or sender name)",
        7: "Bad phone format",
        8: "Can't send message to this number",
        9: "Too many requests",
    }

    def __init__(self, login, password, phones, translit=True):
        super(SMSCSender, self).__init__()
        if not login:
            raise SMSCSenderError("Empty login")
        if not password:
            raise SMSCSenderError("Empty password")

        self._params = {
            "login": urllib.quote(login),  # Login
            "psw": urllib.quote(password),  # Password or MD5 hash
            "phones": urllib.quote(
                PokaYoke.check_phones(phones)
            ),  # Comma or semicolon spaced phone list
            "fmt": 3,  # Response format: 0 - string; 1 - integers; 2 - xml; 3 - json
            "translit": 1 if translit else 0,  # If 1 - transliting message
            "charset": "utf-8",  # Message charset: "windows-1251"|"utf-8"|"koi8-r"
            "cost": 3,  # Message cost in response: 0 - msg; 1 - cost; 2 - msg+cost, 3 - msg+cost+balance
        }

        self._check_account()

    def _get_link(self, **kwargs):
        """Returns get link"""
        params = self._params.copy()
        params.update(kwargs)
        url = self._BASE_URL.format(params=urllib.urlencode(params))

        return url

    def _request_callback(self, code, result, error):
        """Callback for async_get"""
        if code != 200:
            raise SMSCSenderError("RequestError [{}]: {}".format(code, error))
        else:
            try:
                data = json.loads(result)
            except ValueError:
                data = {"error_code": 0, "error": "JSON loads error: {}".format(result)}

            error_code = data.get("error_code")
            if error_code is not None:
                error = self._ERROR_CODES.get(error_code)
                if not error:
                    error = data.get("error", "Unknown error")
                raise SMSCSenderError(
                    "ResponseError [{}]: {}".format(error_code, error)
                )

    def _check_account(self):
        """Send test request to smsc server"""
        url = self._get_link(cost=1, mes=urllib.quote("Hello world!"))
        self._host_api.async_get(url, self._request_callback)

    def text(self, text):
        """Отправка текстового сообщения

        Args:
            text (:obj:`str`): Текст сообщения.
        """

        url = self._get_link(mes=text)

        self._host_api.async_get(url, self._request_callback)


class FtpUploadTracker:
    """Upload progress class"""

    size_written = 0.0
    last_shown_percent = 0

    def __init__(self, file_path, callback, host_api=host):
        self._host_api = host_api
        self.total_size = os.path.getsize(BaseUtils.win_encode_path(file_path))
        self.file_path = file_path
        self.callback = callback

    def handle(self, block):
        """Handler for storbinary

        See Also:
            https://docs.python.org/2/library/ftplib.html#ftplib.FTP.storbinary
        """
        self.size_written += 1024.0
        percent_complete = round((self.size_written / self.total_size) * 100)

        if self.last_shown_percent != percent_complete:
            self.last_shown_percent = percent_complete
            self._host_api.timeout(
                100, lambda: self.callback(self.file_path, int(percent_complete), "")
            )


class FTPSenderError(SenderError):
    """Raises with FTPSender errors"""

    pass


class FTPSender(Sender):
    """Класс для отправки файлов на ftp сервер

    При инициализации проверят подключение к ftp серверу. Файлы отправляет
    по очереди. Максимальный размер очереди можно изменить. Во время
    выполнения передает текущий прогресс отправки файла в callback функцию.

    Note:
        Помимо прогресса в функцию callback может вернуться код ошибки.
            - -1 Файл не существует.
            - -2 Ошибка отправки на ftp, файл будет повторно отправлен.
            - -3 Неизвестная ошибка.


    Args:
        host (:obj:`str`): Адрес ftp сервера.
        port (:obj:`int`, optional): Порт ftp сервера. По умолчанию :obj:`port=21`
        user (:obj:`str`, optional): Имя пользователя. По умолчанию :obj:`"anonymous"`
        passwd (:obj:`str`, optional): Пароль пользователя. По умолчанию :obj:`passwd=""`
        work_dir (:obj:`str`, optional): Директория на сервре для сохранения файлов.
            По умолчанию :obj:`None`
        callback (:obj:`function`, optional): Callable function. По умолчанию :obj:`None`
        queue_maxlen (:obj:`int`, optional): Максимальная длина очереди на отправку.
            По умолчанию :obj:`queue_maxlen=1000`

    Examples:
        >>> def callback(file_path, progress, error):
        >>>     # Пример callback функции, которая отображает
        >>>     # текущий прогресс в счетчике запуска скрипта
        >>>     # Args:
        >>>     #   file_path (str): Путь до файла
        >>>     #   progress (int): Текущий прогресс передачи файла, %
        >>>     #   error (str | Exception): Ошибка при отправке файла, если есть
        >>>     host.stats()["run_count"] = progress
        >>>     if error:
        >>>         host.error(error)
        >>>
        >>>     if progress == 100:
        >>>         host.timeout(3000, lambda: os.remove(BaseUtils.win_encode_path(file_path)))
        >>>
        >>> sender = FTPSender("172.20.0.10", 21, "trassir", "12345", work_dir="/test_dir/", callback=callback)
        >>> sender.files(r"D:\Shots\export_video.avi")
    """

    def __init__(
        self,
        host,
        port=21,
        user="anonymous",
        passwd="",
        work_dir=None,
        callback=None,
        queue_maxlen=1000,
    ):
        super(FTPSender, self).__init__()
        self._host = host
        self._port = port
        self._user = user
        self._passwd = passwd
        self._work_dir = work_dir

        self.queue = deque(maxlen=queue_maxlen)

        self._ftp = None

        if callback is None:
            callback = BaseUtils.do_nothing

        self.callback = callback

        self._work_now = False

        self._check_connection()

    def _check_connection(self):
        """Check if it possible to connect"""
        try:
            ftp = ftplib.FTP()
            ftp.connect(self._host, self._port, timeout=10)
            ftp.login(self._user, self._passwd)
        except ftplib.all_errors as err:
            raise FTPSenderError(err)
        if self._work_dir:
            try:
                ftp.cwd(self._work_dir)
            except ftplib.error_perm:
                ftp.mkd(self._work_dir)
                ftp.cwd(self._work_dir)

        ftp.quit()

    def _get_connection(self):
        """Connecting to ftp

        Returns:
            ftplib.FTP: Ftp object
        """
        try:
            ftp = ftplib.FTP()
            ftp.connect(self._host, self._port, timeout=10)
            ftp.login(self._user, self._passwd)
            if self._work_dir:
                try:
                    ftp.cwd(self._work_dir)
                except ftplib.error_perm:
                    ftp.mkd(self._work_dir)
                    ftp.cwd(self._work_dir)
            ftp.encoding = "utf-8"
            return ftp
        except ftplib.all_errors:
            return

    def _close_connection(self):
        """Close ftp connection"""
        try:
            if self._ftp is not None:
                self._ftp.close()
        finally:
            self._ftp = None

    def _send_file(self, file_path, work_dir=None):
        """Storbinary file with self.ftp

        Args:
            file_path (str): Full file path
            work_dir (str): Work dir on ftp
        """
        if work_dir is not None:
            if self._work_dir:
                work_dir = os.path.normpath("{}/{}".format(self._work_dir, work_dir))
            try:
                self._ftp.cwd(work_dir)
            except ftplib.error_perm:
                self._ftp.mkd(work_dir)
                self._ftp.cwd(work_dir)

        file_name = os.path.basename(file_path)
        upload_tracker = FtpUploadTracker(file_path, self.callback)
        with open(BaseUtils.win_encode_path(file_path), "rb") as opened_file:
            self._ftp.storbinary(
                "STOR " + file_name, opened_file, 1024, upload_tracker.handle
            )

    @BaseUtils.run_as_thread_v2()
    def _sender(self):
        """Send files in queue"""
        if self.queue:
            if self._ftp is None:
                self._ftp = self._get_connection()

            if self._ftp:
                work_dir = None
                file_path = self.queue.popleft()

                if isinstance(file_path, tuple):
                    file_path, work_dir = file_path

                if BaseUtils.is_file_exists(BaseUtils.win_encode_path(file_path)):
                    try:
                        self._send_file(file_path, work_dir)

                    except ftplib.all_errors as err:
                        self._host_api.timeout(
                            100, lambda: self.callback(file_path, -2, error=err)
                        )
                        self.queue.append(file_path)
                        self._close_connection()

                    except Exception as err:
                        self._host_api.timeout(
                            100, lambda: self.callback(file_path, -3, error=err)
                        )

                else:
                    self._host_api.timeout(
                        100,
                        lambda: self.callback(file_path, -1, error="File not found"),
                    )

            self._host_api.timeout(500, self._sender)
        else:
            self._work_now = False
            self._close_connection()

    def files(self, file_paths, *args):
        """Отправка файлов

        Note:
            Можно указать отдельный путь на ftp сервере для каждого файла.
            Для этого список файлов на отправку должен быть приведен к виду
            ``[(shot_path, ftp_path), ...]`` При этом так же будет учитываться
            глобальная папка :obj:`work_dir` заданная при инициализации класса.

        Args:
            file_paths (:obj:`str` | :obj:`list`): Путь до файла или список
                файлов для отправки
        """
        if not isinstance(file_paths, list):
            file_paths = [file_paths]

        self.queue.extend(file_paths)
        if not self._work_now:
            self._work_now = True
            self._sender()


"""
Script
Main functional starts from here
"""

level_name = 'WARNING'
handlers_list = ['host_log', 'alert']

if DEBUG:
    level_name = 'DEBUG'

logger = BaseUtils.get_logger(name=None, host_log=None, popup_log=None, file_log=level_name, file_name=None)


class DatesSupplier:
    def __init__(self):
        self._current_day = datetime.now().date().isoformat()  # "2000-01-01"

    def day_changed(self):
        """Check if day changed"""
        try:
            today = datetime.now().date().isoformat()
            day_changed = self._current_day != today

            if day_changed:
                logger.info("DataManager: day changed {} -> {}".format(self._current_day, today))
                self._current_day = today
            return day_changed, today
        except:
            """Catch exceptions in thread"""
            logger.critical("Check day error", exc_info=True)

    @staticmethod
    def get_dates_of_previous_month():
        month_last_day = (datetime.now() - timedelta(datetime.now().day)).date()
        month_first_day = month_last_day - timedelta(month_last_day.day - 1)
        monthdays = [date(month_first_day.year, month_first_day.month, d).isoformat()
                     for d in xrange(1, month_last_day.day+1)]

        return monthdays


    @staticmethod
    def get_dates_of_previous_week():
        week_day = datetime.now().weekday()
        date_monday = (datetime.now() - timedelta(week_day + 7)).date()
        weekdays = [(date_monday + timedelta(i)).isoformat() for i in xrange(0,7)]

        return weekdays


    @staticmethod
    def get_boundary_trassir_timestamp_of_the_day(date_time_obj=None):
        """
        time_yesterday - datatime obj
        return trassir timestamp of begin and end of the time_yesterday
        """
        if date_time_obj is None:
            date_time_obj = datetime.now() - timedelta(1)
        boundary = ['00:00:00', '23:59:59']
        boundary_trassir_timestamp = []
        for x in boundary:
            dt = datetime.strptime("{}-{}-{} {}".format(date_time_obj.timetuple().tm_year,
                                                        date_time_obj.timetuple().tm_mon,
                                                        date_time_obj.timetuple().tm_mday,
                                                        x), "%Y-%m-%d %H:%M:%S")
            _tstmp = "{}000000".format(int(time.mktime(dt.timetuple())))
            boundary_trassir_timestamp.append(_tstmp)

        return boundary_trassir_timestamp[0], boundary_trassir_timestamp[1]


class DataBaseManager:
    def __init__(self):
        self.db_name = DATA_BASE_NAME
        self.db_login = DATA_BASE_LOGIN
        self.db_pass = DATA_BASE_PASS
        self.metadata = MetaData()
        self.retry_counter = 0

        self.people_quantity = self.initialize_people_quantity_table()
        self.connection = self.connect_to_database()
        self.persisting_the_tables()

    def connect_to_database(self):
        self.engine = create_engine(
            'postgresql://{}:{}@localhost:5432/{}'.format(self.db_login,
                                                          self.db_pass,
                                                          self.db_name
                                                          ))
        try:
            return self.engine.connect()
        except Exception as err:
            raise ValueError(unicode(err[0], 'cp1251'))



    def persisting_the_tables(self):
        """
        Создаёт таблицу, если её нет
        """
        self.metadata.create_all(self.engine)

    def initialize_people_quantity_table(self):
        _people_quantity = Table('people quantity detection', self.metadata,
                                     Column('event_id', BigInteger(), primary_key=True, autoincrement=True),
                                     Column('channel_name', Unicode()),
                                     Column('zone_name', Unicode()),
                                     Column('people_quantity', Integer()),
                                     Column('event_ts', BigInteger()),
                                     )
        return _people_quantity

    def insert_data(self, channel_name, zone_name, people_quantity, event_ts):
        ins = self.people_quantity.insert().values(
            channel_name=channel_name,
            zone_name=zone_name,
            people_quantity=people_quantity,
            event_ts=event_ts
        )
        result = self.connection.execute(ins)
        logger.info(result)

    def add_data(self, data):
        """
        Format data before insert to database
        """
        if not isinstance(data, dict):
            return
        event_ts = data.get('event_ts')
        channel_name = data.get('channel_name')
        people_quantity = data.get('people_quantity')
        zone_name = data.get('zone_name')
        if not (event_ts and channel_name and people_quantity and zone_name):
            return
        logger.debug('Start insert data in DB')
        self.insert_data(channel_name, zone_name, people_quantity, event_ts)

    def _get_data(self, begin_ts, end_ts):
        """
        makes  querys to database to get information
        """
        #s = select([self.people_quantity]).where(self.people_quantity.c.event_ts.between(begin_ts,end_ts))
        s = select([self.people_quantity]).where(
                and_(self.people_quantity.c.event_ts > begin_ts,
                     self.people_quantity.c.event_ts < end_ts
                     )
                )
        s = s.order_by(self.people_quantity.c.event_ts)
        rp = self.connection.execute(s)
        return rp.fetchall()

    def get_data(self, date):
        """
        :param date: str '2019-05-31'
        :return: response from data base [(),]
        """
        begin_ts, end_ts = DatesSupplier.get_boundary_trassir_timestamp_of_the_day(
            datetime.strptime(date, '%Y-%m-%d').date())
        res = self._get_data(begin_ts, end_ts)
        return res

    def get_data_wide_range(self):
        """
        Возвращает выборку из БД по времени, временной диапазон:
        timestamp начала вчерашнего дня, timestamp - конца текущей даты
        """
        today_date = datetime.now().date()
        yestarday_date = today_date - timedelta(days=1)
        begin_ts = DatesSupplier.get_boundary_trassir_timestamp_of_the_day(yestarday_date)[0]
        end_ts = DatesSupplier.get_boundary_trassir_timestamp_of_the_day(today_date)[1]
        res = self._get_data(begin_ts, end_ts)
        return res


class Reporter:
    def __init__(self, mail_api, data_loader, get_data_wide_range):
        self._mail_api = mail_api
        self._data_loader = data_loader
        self._wide_range_data_loader = get_data_wide_range
        self.report_path = Reporter.reports_folder()

    @staticmethod
    def reports_folder():
        screenshot_folder = BaseUtils.get_screenshot_folder()
        _report_folder = os.path.join(screenshot_folder,'People_reports')
        try:
            os.mkdir(_report_folder)
        except OSError as err:
            logger.error('Folder allready exists: %s' % err)
        return _report_folder

    def _send_report(self, filepaths_list):
        """Send report to mail

        Args:
            filepaths_list (list) : list of full path to file
        """
        logger.info("Reporter: send file {}".format(json.dumps(filepaths_list)))
        host.stats()["run_count"] += 1
        self._mail_api.files(filepaths_list)

    @staticmethod
    def _get_yesterday_week(yesterday):
        """Returns (list) days of the week in isoformat '%Y-%m-%d'

        Args:
            yesterday (date) : Yesterday
        """
        weekdays = [(yesterday + timedelta(days=i)).isoformat()
                    for i in xrange(0 - yesterday.weekday(), 7 - yesterday.weekday())]
        return weekdays

    @staticmethod
    def _get_yesterday_month(yesterday):
        """Returns (list) days of the month in isoformat '%Y-%m-%d'

        Args:
            yesterday (date) : Yesterday
        """
        month_days = calendar.monthrange(yesterday.year, yesterday.month)[1]
        monthdays = [date(yesterday.year, yesterday.month, d).isoformat() for d in xrange(1, month_days + 1)]
        return monthdays

    @staticmethod
    def ts_to_datetime(ts):
        return datetime.fromtimestamp(ts / 10 ** 6)


    def _load_data(self, report_days):
        """Returns data from database"""
        """
        _data_loader - возвращает список словарей
        [{'event_ts': 1559320756562406L, 'channel_name': '\xd0\x9e\xd1\x87\xd0\xb5\xd1\x80\xd0\xb5\xd0\xb4\xd1\x8c1',
         'person_quantity': 6, 'zone_name': 'Default Zone'}, ...]
        """
        data = {day: self._data_loader(day) for day in report_days}
        return data

    def _prepare_report_params(self, today_str):
        """Preparing dates for report"""
        today = datetime.strptime(today_str, '%Y-%m-%d').date()
        yesterday = today - timedelta(days=1)
        report_days = {"daily": [yesterday.isoformat(), today_str]}

        if today.weekday() == 0:
            report_days["weekly"] = self._get_yesterday_week(yesterday)
        if today.day == 1:
            report_days["monthly"] = self._get_yesterday_month(yesterday)

        return report_days, yesterday

    def _make_xlsx(self, report_type, report_data, report_day):
        """Create report in *.xlsx format

        Args:
            report_type (str) : daily/weekly/monthly (used in filename)
            report_data (dict) : All data for report
            report_day (str) : Day for report (used in filename)
        """
        filename = "{}_{}.xlsx".format(report_type, report_day)
        path = os.path.join(self.report_path, filename)
        logger.debug("Reporter: creating {}".format(filename))

        workbook = xlsxwriter.Workbook(path)

        cell_format = {
            "hat": workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'font_size': 14,
            }),
            "header": workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'vcenter',
                'text_wrap': True,
                'border': 1,
            }),
            "text": workbook.add_format({
                'valign': 'vcenter',
                'align': 'center',
                'border': 1,
            }),
            "black": workbook.add_format({
                'font_color': 'black',
            }),
            "grey": workbook.add_format({
                'font_color': '#b2b2b2',
            }),
            "date": workbook.add_format({
                'num_format': 'yyyy.mm.dd',
                'align': 'center',
                'valign': 'vcenter',
                'border': 1,
            }),
            "time": workbook.add_format({
                'num_format': 'hh:mm:ss',
                'align': 'center',
                'valign': 'vcenter',
                'border': 1,
            }),
        }

        for day in sorted(report_data.keys()):
            data = report_data[day]

            worksheet = workbook.add_worksheet(day)
            worksheet.set_column(0, 0, 5)
            worksheet.set_column(1, 2, 12)
            worksheet.set_column(3, 3, 25)
            worksheet.set_column(4, 5, 15)

            if data:
                worksheet.merge_range(0, 0, 0, 5,
                                      u'Количество записей в отчете: {}'.format(len(data)),
                                      cell_format["hat"]
                                      )

                worksheet.write(1, 0, u"№", cell_format["header"])
                worksheet.write(1, 1, u"Дата", cell_format["header"])
                worksheet.write(1, 2, u"Время", cell_format["header"])
                worksheet.write(1, 3, u"Название канала", cell_format["header"])
                worksheet.write(1, 4, u"Название зоны", cell_format["header"])
                worksheet.write(1, 5, u"Кол-во людей", cell_format["header"])

            else:
                worksheet.merge_range(0, 0, 0, 5,
                                      u'Днные отсутствуют',
                                      cell_format["hat"])

            for idx, row in enumerate(data, 2):

                # row = (18841L, u'\u041e\u0447\u0435\u0440\u0435\u0434\u044c1', u'Cash1', 3, 1559459961420148L)

                dt = self.ts_to_datetime(row[-1])

                worksheet.write(idx, 0, idx - 1, cell_format['text'])
                worksheet.write(idx, 1, dt, cell_format['date'])
                worksheet.write(idx, 2, dt, cell_format['time'])
                worksheet.write(idx, 3, row[1], cell_format['text'])
                worksheet.write(idx, 4, row[2], cell_format['text'])
                worksheet.write(idx, 5, row[3], cell_format['text'])

        workbook.close()

        return path

    def generate_reports(self, today_str, wide_range=False):
        """Generate reports for yesterday
        Точка входа
        "" Если wide_range == True, то отчет формируется за предыдущие сутки
         +  всё что накопилось на текущий момент

        Args:
            today_str (str) : Today date in isoformat "%Y-%m-%d"
            report_days = {'monthly': ['2019-04-01',  ..., '2019-04-30'],
                           'weekly': ['2019-04-22', ..., '2019-04-28'],
                           'daily': ['2019-04-30']}
        """
        try:
            if not wide_range:
                report_days, yesterday = self._prepare_report_params(today_str)
                report_data = {report_type: self._load_data(report_dates)
                               for report_type, report_dates in report_days.iteritems()}

            else:
                yesterday = datetime.now().date()
                report_data = {'wide_range': {datetime.now().date().isoformat(): self._wide_range_data_loader()}}

            reports = [self._make_xlsx(report_type, report_data, yesterday.isoformat())
                       for report_type, report_data in report_data.iteritems()]

            self._send_report(reports)

        except:
            """Catch exceptions in thread"""
            logger.critical("Generate reports error", exc_info=True)


class DeepDetectionHandler:
    """ Listen for deep detections events and sent to database through callback """

    def __init__(self, dd_callback, host_api=host):
        self._host_api = host_api
        self._dd_callback = dd_callback
        self.confidence = CONFIDENCE

        self.channels = []
        self.zones = []
        self.objects_types = []

        self.set_channels(CHANNELS)
        self.set_zones(ZONES)
        self.set_objects_types()

    def not_static_method(self):
        pass

    def _get_obj_name(self, guid):
        """Returns object name
        Args:
            guid (str) : Trassir object guid
        """
        try:
            name = self._host_api.object(guid).name
        except EnvironmentError:
            """Object not found"""
            name = guid
        return name

    def set_objects_types(self):
        """
        object_type is str: 'person', 'head', 'vehicle', 'bicycle'
        """
        self.objects_types.append('person')
        self.objects_types.append('head')

    def set_channels(self, chan_names=None):
        """
        chan_names - str, названия каналов через запятую
        """
        assert chan_names, 'Необходимо выбрать имена каналов для работы!'
        self.channels = self.get_channels_guid_list(chan_names)
        logger.debug('chans_guid_list for deep detections: %s' % self.channels)

    def get_channels_guid_list(self, chan_names):
        """
        chan_names - str, названия каналов через запятую, возвращается список гуидов каналов
        """
        self.not_static_method()

        channels_guids_list = None
        try:
            chan_names_list = chan_names.split(',')
            channels_guids_list = list({x[1] for x in objects_list("Channel")
                                        if x[0] in chan_names_list})
        except Exception as err:
            logger.debug(err)
        return channels_guids_list

    def set_zones(self, deep_detections_zones_parameter=None):
        # Имена зон общие для всех каналов
        assert deep_detections_zones_parameter, 'Укажите имена зон для работы!'
        self.zones = deep_detections_zones_parameter.split(",")

    def is_channel_in_channels(self, chan_guid):
        if not self.channels:
            return True
        if chan_guid in self.channels:
            return True

    def is_zone_name_in_zones(self, zone_name):
        if zone_name in self.zones:
            return True

    def is_confidence_good(self, confidence):
        if confidence * 100 >= self.confidence:
            return True

    def is_detected_object_type_good(self, object_type):
        if object_type in self.objects_types:
            return True

    def is_zone_type_is_good(self, zone_type):
        """
        zone_type: if 0 is zone, if 1 iz border
        """
        if zone_type == 0:
            return True

    def get_detected_objects_quantity(self, event):
        """
        Считаем количество объектов на канале по каждой зоне, имя которой
        есть в списке допустимых имен зон self.zones
        Возвращаем словарь: {zone_name, objects_count}
        """

        detected_objects_quantity_by_zones = dict()

        for zone in event.zones:
            if not self.is_zone_name_in_zones(zone.zone_name):
                continue
            logger.debug('zone %s in list, continue' % zone.zone_name)
            if not self.is_zone_type_is_good(zone.zone_type):
                continue
            logger.debug('%s. zone type is good, continue' % zone.zone_name)

            detected_objects_quantity = 0
            for obj_inside in zone.objects_inside:
                if not self.is_confidence_good(obj_inside.confidence):
                    continue
                logger.debug('%s. confidence is good, continue' % zone.zone_name)

                if not self.is_detected_object_type_good(obj_inside.class_id):
                    continue
                logger.debug('%s. detected object type is good, continue' % zone.zone_name)
                detected_objects_quantity += 1
            logger.debug('%s. %s. ПОСЧИТАНО ЛЮДЕЙ: %s' % (unicode(object(event.channel_guid).name),
                                                          zone.zone_name,
                                                          detected_objects_quantity))
            if not detected_objects_quantity:
                continue
            detected_objects_quantity_by_zones[zone.zone_name] = detected_objects_quantity

        if detected_objects_quantity_by_zones:
            logger.debug('return to handler: %s' % detected_objects_quantity_by_zones)
            return detected_objects_quantity_by_zones

    def handler(self, event):
        """
        deep detections events handler
        точка входа
        """
        if not self.is_channel_in_channels(event.channel_guid):
            # logger.debug("channel name: %s channel guid %s not in channels list %s. " % (object(event.channel_guid).name, event.channel_guid, self.channels))
            return
        logger.debug('channel %s in channels list. Continue' % event.channel_guid)
        detected_objects_quantity_by_zones = self.get_detected_objects_quantity(event)
        if not detected_objects_quantity_by_zones:
            return

        for zone_name, objects_quantity in detected_objects_quantity_by_zones.iteritems():
            logger.debug(unicode(object(event.channel_guid).name))
            prepared_data = dict()
            prepared_data["channel_name"] = unicode(object(event.channel_guid).name)
            prepared_data["event_ts"] = event.event_ts
            prepared_data["zone_name"] = zone_name
            prepared_data["people_quantity"] = objects_quantity
            logger.debug('prepared_data: %s' % prepared_data)
            self._dd_callback(prepared_data)


class Manager:
    """

    """

    def __init__(self):
        self._current_day = datetime.now().date().isoformat()  # "2000-01-01"

    def day_changed(self):
        """Check if day changed"""
        try:
            today = datetime.now().date().isoformat()
            day_changed = self._current_day != today

            if day_changed:
                logger.info("DataManager: day changed {} -> {}".format(self._current_day, today))
                self._current_day = today
            return day_changed, today
        except:
            """Catch exceptions in thread"""
            logger.critical("Check day error", exc_info=True)


def make_wide_range_report():
    colors = {"Красный": "Red", "Зелёный": "Green", "Синий": "Blue"}
    if object(SCHEDULE).state("color") == colors[SCHEDULE_COLOR]:
        date_now = datetime.now().date().isoformat()
        reporter.generate_reports(date_now, wide_range=True)

@BaseUtils.run_as_thread
def worker(check_day, reports_generator, first_export=True):
    """Work iteration

    Check if day changed every second
    Start export if day changed or first_export = True
    Working in thread to avoid sleep main trassir process

    Args:
        check_day (func) : Must return True/False if day changed or not and current day
        reports_generator (func) : Starting generate report when day changed
        first_export (bool, optional) : Start export after start script
    """
    if first_export:
        date_now = datetime.now().date().isoformat()
        reporter.generate_reports(date_now, wide_range=True)
        check_day()

    while True:
        day_changed, current_day = check_day()
        if day_changed:
            reports_generator(current_day)
        time.sleep(1)


"""
Start here
"""
mail_sender = EmailSender(account=MAIL_ACCOUNT, mailing_list=MAIL_RECIPIENTS, subject=MAIL_SUBJECT)
dates_suplier = DatesSupplier()

db_manager = DataBaseManager()
reporter = Reporter(mail_sender,
                    db_manager.get_data,
                    db_manager.get_data_wide_range)

deep_detection_handler = DeepDetectionHandler(dd_callback=db_manager.add_data)
activate_on_deep_detection_events(deep_detection_handler.handler)


# ACTIVATION BY SCHEDULE
if ENABLE_SCHEDULE:
    assert SCHEDULE, 'Необходимо выбрать расписание!'
    assert SCHEDULE_COLOR, 'Необходимо выбрать цвет распивания для работы!'
    report_schedule = object(SCHEDULE)
    report_schedule.activate_on_state_changes(make_wide_range_report)

worker(
    check_day=dates_suplier.day_changed,
    reports_generator= reporter.generate_reports,
    first_export=EXPORT_AT_STARTUP,
)
