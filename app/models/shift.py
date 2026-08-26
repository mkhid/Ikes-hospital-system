"""Shift definitions. Times are configurable by an administrator."""
from datetime import datetime, timedelta

from app.extensions import db


class Shift(db.Model):
    """
    One of the three daily shift windows.

    Seeded to the project brief: Morning 07:00-14:00, Afternoon 14:00-22:00 and
    Night 22:00-07:00. The night shift crosses midnight, so its end time falls
    on the following calendar day.
    """

    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False, index=True)
    name = db.Column(db.String(48), nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    crosses_midnight = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    @property
    def duration_minutes(self):
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        if self.crosses_midnight or end <= start:
            end += 24 * 60
        return end - start

    @property
    def duration_hours(self):
        return self.duration_minutes / 60.0

    def start_datetime(self, work_date):
        """Absolute start of this shift on the given roster date."""
        return datetime.combine(work_date, self.start_time)

    def end_datetime(self, work_date):
        """Absolute end, rolling into the next day for the night shift."""
        end = datetime.combine(work_date, self.end_time)
        if self.crosses_midnight:
            end += timedelta(days=1)
        return end

    @property
    def window_label(self):
        return f"{self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"

    @property
    def short_name(self):
        return self.name.replace(" Shift", "")

    def __repr__(self):
        return f"<Shift {self.code}>"
