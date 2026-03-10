RECORD_BOOK_TO_FIO = {
    "20220805": "Абдужабборов Мухаммаджон Акмалович",
    "20220806": "Белов Ярослав Андреевич",
    "20220808": "Ведмеденко Максим Михайлович",
    "20220809": "Ворушило Александра Всеволодовна",
    "20220810": "Гребнева Екатерина Олеговна",
    "20220812": "Гусев Владислав Павлович",
    "20220813": "Жерлицын Иван Игоревич",
    "20220814": "Зимин Роман Аркадьевич",
    "20220815": "Идиева Хамида Саидвалиевна",
    "20220816": "Кабирова Юлия Раисовна",
    "20220818": "Колова Эвелина Андреевна",
    "20220819": "Кононов Константин Витальевич",
    "20220820": "Косыхина Анна Николаевна",
    "20220821": "Крюгер Андрей Олегович",
    "20220822": "Кульков Георгий Максимович",
    "20220823": "Лысов Андрей Григорьевич",
    "20220824": "Меньшиков Иван Леонидович",
    "20220826": "Панфилов Кирилл Сергеевич",
    "20220828": "Пивоваров Вадим Андреевич",
    "20220829": "Полубояров Валерий Евгеньевич",
    "20220831": "Софронов Артём Вячеславович",
    "20220833": "ТРУБАЧЕВ ДМИТРИЙ ВЯЧЕСЛАВОВИЧ",
    "20220834": "Тыкин Лев Сергеевич",
    "20220835": "Федоров Иван Игоревич",
    "20221061": "Шибанов Никита Алексеевич",
}

def get_fio_by_record_book(rb: str) -> str:
    """Returns 'FIO (record_book)' or just 'record_book' if FIO is unknown."""
    fio = RECORD_BOOK_TO_FIO.get(str(rb))
    if fio:
        return f"{fio} ({rb})"
    return str(rb)

def get_short_fio_by_record_book(rb: str) -> str:
    """Returns 'Lastname F.O. (record_book)' or just 'record_book'."""
    fio = RECORD_BOOK_TO_FIO.get(str(rb))
    if not fio:
        return str(rb)
        
    parts = fio.split()
    if len(parts) >= 3:
        last_name = parts[0].capitalize()
        first_in = parts[1][0].upper()
        patronymic_in = parts[2][0].upper()
        short_fio = f"{last_name} {first_in}.{patronymic_in}."
        return f"{short_fio} ({rb})"
    elif len(parts) == 2:
         last_name = parts[0].capitalize()
         first_in = parts[1][0].upper()
         short_fio = f"{last_name} {first_in}."
         return f"{short_fio} ({rb})"
    else:
        return f"{fio} ({rb})"
