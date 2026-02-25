import sys
sys.path.append("/home/aptem/bb_schedule")

from app.bot.handlers.session import format_results

sample_data = [
    {
        'semester': '1 семестр',
        'subject': 'Математика',
        'grade': 'Отлично',
        'date': '2024-01-15',
        'grade_value': 5,
        'is_exam': True,
        'passed': True
    },
    {
        'semester': '1 семестр',
        'subject': 'Физика',
        'grade': 'Хорошо',
        'date': '2024-01-16',
        'grade_value': 4,
        'is_exam': True,
        'passed': True
    },
    {
        'semester': '3 семестр',
        'subject': 'Программирование',
        'grade': 'Зачтено',
        'date': '2025-01-17',
        'grade_value': None,
        'is_exam': False,
        'passed': True
    },
    {
        'semester': '2 семестр (2024/2025)',
        'subject': 'Алгебра',
        'grade': 'Недопуск',
        'date': '',
        'grade_value': None,
        'is_exam': False,
        'passed': False
    }
]

settings = {}
output = format_results(sample_data, settings)
print(output)
