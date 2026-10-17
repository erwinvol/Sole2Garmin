"""FIT file builder and device enhancer for Sole activities to unlock Garmin Physio TrueUp."""

import logging
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_timestamp_ms(date_val: Any) -> int:
    """Parse various timestamp representations into unix milliseconds."""
    from src.garmin_client import parse_activity_datetime

    dt = parse_activity_datetime(date_val)
    if dt:
        return int(dt.timestamp() * 1000)
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class FitBuilder:
    """Builds and enhances FIT files for Sole treadmill, elliptical, and bike workouts."""

    def __init__(
        self,
        spoof_device: bool = True,
        device_product_id: int = 3943,  # Garmin Epix Gen 2 (or 4315 Forerunner 965)
        software_version: float = 18.16,
    ):
        self.spoof_device = spoof_device
        self.device_product_id = device_product_id
        self.software_version = software_version

    def build_fit_from_activity(
        self,
        activity: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> str:
        """Construct a complete binary FIT file from a standardized Sole activity.

        Args:
            activity: Normalized activity dictionary.
            output_path: Destination path. Defaults to a temporary file.

        Returns:
            Absolute path to the created FIT file.
        """
        # If the activity already has a raw FIT file path, enhance it
        raw_fit = activity.get("raw_fit_path")
        if raw_fit and os.path.exists(raw_fit):
            logger.info("Activity %s has raw FIT file; applying device enhancement...", activity["id"])
            return self.enhance(raw_fit, output_path)

        from fit_tool.fit_file_builder import FitFileBuilder
        from fit_tool.profile.messages.file_id_message import FileIdMessage
        from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
        from fit_tool.profile.messages.event_message import EventMessage
        from fit_tool.profile.messages.sport_message import SportMessage
        from fit_tool.profile.messages.record_message import RecordMessage
        from fit_tool.profile.messages.lap_message import LapMessage
        from fit_tool.profile.messages.session_message import SessionMessage
        from fit_tool.profile.messages.activity_message import ActivityMessage
        from fit_tool.profile.profile_type import (
            ActivityType,
            Event,
            EventType,
            FileType,
            Intensity,
            LapTrigger,
            Manufacturer,
            SessionTrigger,
            Sport,
            SubSport,
        )

        dest_path = output_path or os.path.join(
            tempfile.gettempdir(), f"sole_{activity['id']}.fit"
        )

        start_time_ms = parse_timestamp_ms(activity.get("date", ""))
        duration_s = max(float(activity.get("duration_seconds", 0.0)), 10.0)
        end_time_ms = start_time_ms + int(duration_s * 1000)
        distance_m = max(float(activity.get("distance_meters", 0.0)), 10.0)
        calories = int(activity.get("calories", 0))

        avg_speed_mps = float(activity.get("avg_speed_mps") or (distance_m / duration_s))
        max_speed_mps = float(activity.get("max_speed_mps") or (avg_speed_mps * 1.15))

        avg_hr = int(activity.get("avg_heart_rate") or 140)
        max_hr = int(activity.get("max_heart_rate") or (avg_hr + 15))

        avg_cadence = int(activity.get("avg_cadence") or 160)
        max_cadence = int(activity.get("max_cadence") or (avg_cadence + 10))

        avg_incline = float(activity.get("avg_incline_pct") or 1.0)
        total_ascent = int(activity.get("total_ascent_meters") or (distance_m * (avg_incline / 100.0)))

        # Resolve Sport & SubSport
        sport_str = activity.get("sport", "RUNNING").upper()
        sub_sport_str = activity.get("sub_sport", "").upper()

        if sport_str == "CYCLING":
            fit_sport = Sport.CYCLING
            fit_sub_sport = SubSport.INDOOR_CYCLING
            sport_name = "Indoor Cycling"
            act_type = ActivityType.CYCLING
        elif sport_str == "FITNESS_EQUIPMENT":
            fit_sport = Sport.FITNESS_EQUIPMENT
            fit_sub_sport = SubSport.ELLIPTICAL
            sport_name = "Elliptical"
            act_type = ActivityType.FITNESS_EQUIPMENT
        elif sport_str == "WALKING":
            fit_sport = Sport.WALKING
            if sub_sport_str in ("INDOOR_WALKING", "INDOOR"):
                fit_sub_sport = SubSport.INDOOR_WALKING
                sport_name = "Indoor Walking"
            else:
                fit_sub_sport = SubSport.GENERIC
                sport_name = "Walking"
            act_type = ActivityType.WALKING
        else:  # RUNNING
            fit_sport = Sport.RUNNING
            if sub_sport_str in ("INDOOR_RUNNING", "INDOOR"):
                fit_sub_sport = SubSport.INDOOR_RUNNING
                sport_name = "Indoor Running"
            elif sub_sport_str == "TREADMILL":
                fit_sub_sport = SubSport.TREADMILL
                sport_name = "Treadmill"
            else:
                fit_sub_sport = SubSport.INDOOR_RUNNING
                sport_name = "Indoor Running"
            act_type = ActivityType.RUNNING

        builder = FitFileBuilder(auto_define=True)

        # 1. FileId: Spoof Garmin Epix Gen 2 / Running Watch
        file_id = FileIdMessage()
        file_id.type = FileType.ACTIVITY
        file_id.manufacturer = Manufacturer.GARMIN.value
        file_id.product = self.device_product_id
        file_id.serial_number = 3456789123
        file_id.time_created = start_time_ms
        builder.add(file_id)

        # 2. DeviceInfo
        dev_info = DeviceInfoMessage()
        dev_info.device_index = 0
        dev_info.manufacturer = Manufacturer.GARMIN.value
        dev_info.product = self.device_product_id
        dev_info.software_version = self.software_version
        dev_info.timestamp = start_time_ms
        builder.add(dev_info)

        # 3. Timer Start Event
        ev_start = EventMessage()
        ev_start.timestamp = start_time_ms
        ev_start.event = Event.TIMER
        ev_start.event_type = EventType.START
        builder.add(ev_start)

        # 4. Sport
        sport_msg = SportMessage()
        sport_msg.sport = fit_sport
        sport_msg.sub_sport = fit_sub_sport
        sport_msg.sport_name = sport_name
        builder.add(sport_msg)

        # 5. Records (Time-series)
        data_points = activity.get("data_points") or []
        if data_points:
            # Use real data points
            for pt in data_points:
                rec = RecordMessage()
                rec.timestamp = parse_timestamp_ms(pt.get("timestamp", "")) or start_time_ms
                if "distance" in pt:
                    rec.distance = float(pt["distance"])
                if "speed" in pt:
                    rec.speed = float(pt["speed"])
                if "heart_rate" in pt:
                    rec.heart_rate = int(pt["heart_rate"])
                if "cadence" in pt:
                    rec.cadence = int(pt["cadence"])
                if "altitude" in pt:
                    rec.altitude = float(pt["altitude"])
                builder.add(rec)
        else:
            # Synthesize realistic records at intervals
            step_seconds = 5 if duration_s > 1800 else 2
            num_steps = max(int(duration_s / step_seconds), 2)
            dist_per_step = distance_m / num_steps
            curr_dist = 0.0
            curr_alt = 100.0  # Base elevation 100m
            alt_step = (total_ascent / num_steps) if num_steps > 0 else 0.0

            for i in range(num_steps + 1):
                sec_elapsed = min(i * step_seconds, duration_s)
                rec = RecordMessage()
                rec.timestamp = start_time_ms + int(sec_elapsed * 1000)
                rec.distance = round(curr_dist, 2)
                rec.speed = round(avg_speed_mps, 2)

                if avg_hr > 0:
                    # Slight sinusoidal variation for realistic HR curve
                    hr_variation = int(3 * math.sin(i / 10.0))
                    rec.heart_rate = min(max_hr, max(avg_hr - 5, avg_hr + hr_variation))

                if avg_cadence > 0:
                    rec.cadence = avg_cadence

                rec.altitude = round(curr_alt, 2)
                builder.add(rec)

                curr_dist += dist_per_step
                curr_alt += alt_step

        # 6. Timer Stop Event
        ev_stop = EventMessage()
        ev_stop.timestamp = end_time_ms
        ev_stop.event = Event.TIMER
        ev_stop.event_type = EventType.STOP_ALL
        builder.add(ev_stop)

        # 7. Lap
        lap = LapMessage()
        lap.timestamp = end_time_ms
        lap.start_time = start_time_ms
        lap.total_elapsed_time = duration_s
        lap.total_timer_time = duration_s
        lap.total_distance = distance_m
        lap.avg_speed = avg_speed_mps
        lap.max_speed = max_speed_mps
        lap.total_calories = calories
        lap.avg_heart_rate = avg_hr
        lap.max_heart_rate = max_hr
        lap.avg_cadence = avg_cadence
        lap.max_cadence = max_cadence
        lap.total_ascent = total_ascent
        lap.sport = fit_sport
        lap.sub_sport = fit_sub_sport
        lap.intensity = Intensity.ACTIVE
        lap.lap_trigger = LapTrigger.SESSION_END
        builder.add(lap)

        # 8. Session
        sess = SessionMessage()
        sess.timestamp = end_time_ms
        sess.start_time = start_time_ms
        sess.total_elapsed_time = duration_s
        sess.total_timer_time = duration_s
        sess.total_distance = distance_m
        sess.avg_speed = avg_speed_mps
        sess.max_speed = max_speed_mps
        sess.total_calories = calories
        sess.avg_heart_rate = avg_hr
        sess.max_heart_rate = max_hr
        sess.avg_cadence = avg_cadence
        sess.max_cadence = max_cadence
        sess.total_ascent = total_ascent
        sess.sport = fit_sport
        sess.sub_sport = fit_sub_sport
        sess.num_laps = 1
        sess.first_lap_index = 0
        sess.trigger = SessionTrigger.ACTIVITY_END
        builder.add(sess)

        # 9. Activity
        act_msg = ActivityMessage()
        act_msg.timestamp = end_time_ms
        act_msg.total_timer_time = duration_s
        act_msg.num_sessions = 1
        act_msg.type = act_type
        act_msg.event = Event.ACTIVITY
        act_msg.event_type = EventType.STOP
        builder.add(act_msg)

        fit_file = builder.build()
        with open(dest_path, "wb") as f:
            f.write(fit_file.to_bytes())

        logger.info(
            "Synthesized FIT file for activity %s -> %s (Device: Epix Gen 2 ID %d, Size: %d bytes)",
            activity["id"],
            dest_path,
            self.device_product_id,
            os.path.getsize(dest_path),
        )
        return dest_path

    def enhance(self, input_path: str, output_path: Optional[str] = None) -> str:
        """Enhance an existing raw FIT file with Garmin Epix Gen 2 device signatures."""
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Source FIT file not found: {input_path}")

        if not self.spoof_device:
            logger.info("Device spoofing disabled; using original FIT file.")
            return input_path

        from fit_tool.fit_file import FitFile
        from fit_tool.profile.messages.file_id_message import FileIdMessage
        from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
        from fit_tool.profile.messages.sport_message import SportMessage
        from fit_tool.profile.profile_type import FileType, Manufacturer, Sport, SubSport

        dest_path = output_path or os.path.join(
            tempfile.gettempdir(), f"enhanced_{os.path.basename(input_path)}"
        )

        with open(input_path, "rb") as f:
            raw_bytes = f.read()

        fit_file = FitFile.from_bytes(raw_bytes, check_crc=False)

        for record in fit_file.records:
            msg = record.message
            if isinstance(msg, FileIdMessage):
                msg.type = FileType.ACTIVITY
                msg.manufacturer = Manufacturer.GARMIN.value
                msg.product = self.device_product_id
            elif isinstance(msg, DeviceInfoMessage):
                msg.manufacturer = Manufacturer.GARMIN.value
                msg.product = self.device_product_id
                msg.software_version = self.software_version
            elif isinstance(msg, SportMessage):
                if msg.sport is None:
                    msg.sport = Sport.RUNNING
                    msg.sub_sport = SubSport.TREADMILL

        with open(dest_path, "wb") as f:
            f.write(fit_file.to_bytes())

        logger.info("Patched existing FIT file -> %s (Device: %d)", dest_path, self.device_product_id)
        return dest_path

    @staticmethod
    def cleanup_file(file_path: str) -> None:
        """Safely remove a temporary file."""
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception as exc:
            logger.debug("Failed to remove temp file %s: %s", file_path, exc)
