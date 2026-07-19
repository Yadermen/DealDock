from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    language = State()
    region_group = State()
    region = State()


class QuietHoursSetup(StatesGroup):
    start_hour = State()
    start_minute = State()
    end_hour = State()
    end_minute = State()
    confirm = State()


class DigestSetup(StatesGroup):
    time_hour = State()
    time_minute = State()
    weekday = State()
    confirm = State()


class RegionCompare(StatesGroup):
    selecting = State()


class AdminPremium(StatesGroup):
    search = State()
    manual_days = State()
    exact_until = State()
    confirm = State()


class AddGame(StatesGroup):
    query = State()
    select = State()
    rule = State()
    value = State()


class EditWatch(StatesGroup):
    value = State()


class PremiumFilterSetup(StatesGroup):
    value = State()


class AdminGiveaway(StatesGroup):
    title = State()
    url = State()


class AdminBroadcast(StatesGroup):
    content = State()
    confirm = State()


class AdminGameRefresh(StatesGroup):
    app_id = State()
