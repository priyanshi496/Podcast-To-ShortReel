import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

class CameraMotionEstimator:
    def __init__(self, ema_alpha: float = 0.05, min_features: int = 10, min_inliers: int = 5):
        self.ema_alpha = ema_alpha
        self.min_features = min_features
        self.min_inliers = min_inliers
        self.prev_gray = None
        self.accumulated_drift_x = 0.0
        self.low_pass_drift_x = 0.0
        self.shake_x = 0.0
        
        # Initialize ORB detector
        # Limit to 300 features to maintain fast execution at 10 FPS
        self.orb = cv2.ORB_create(nfeatures=300)  # type: ignore[attr-defined]
        
    def update(self, frame: np.ndarray, person_bboxes: list):
        """
        Estimates camera motion in the current frame and updates the internal high-frequency shake estimate.
        This should be called exactly once per frame.
        """
        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        if self.prev_gray is None:
            # First frame in the scene
            self.prev_gray = gray
            self.accumulated_drift_x = 0.0
            self.low_pass_drift_x = 0.0
            self.shake_x = 0.0
            return
            
        # Create background mask (exclude areas around person bounding boxes)
        mask = np.ones(gray.shape, dtype=np.uint8) * 255
        for box in person_bboxes:
            # box format: (x1, y1, w, h)
            x, y, w, h = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            # Add 15% padding to ensure keypoints on clothing/body boundaries are not tracked
            pad_w = int(w * 0.15)
            pad_h = int(h * 0.15)
            x1 = max(0, x - pad_w)
            y1 = max(0, y - pad_h)
            x2 = min(gray.shape[1], x + w + pad_w)
            y2 = min(gray.shape[0], y + h + pad_h)
            mask[y1:y2, x1:x2] = 0
            
        # Detect ORB keypoints on the previous frame's background
        kp = self.orb.detect(self.prev_gray, mask=mask)
        
        if len(kp) < self.min_features:
            # Too few background features found; skip tracking updates for this frame
            self.prev_gray = gray
            return
            
        # Convert keypoints to float32 coordinates for optical flow
        prev_pts = np.array([k.pt for k in kp], dtype=np.float32).reshape(-1, 1, 2)
        
        # Track features to current frame using Lucas-Kanade optical flow
        curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, prev_pts, None)  # type: ignore[arg-type]
        
        if curr_pts is not None and len(curr_pts) > 0:
            status_flat = status.flatten()
            good_prev = prev_pts[status_flat == 1]
            good_curr = curr_pts[status_flat == 1]
            
            if len(good_prev) >= self.min_features:
                # Estimate horizontal translation tx using partial affine transform
                M, inliers = cv2.estimateAffinePartial2D(good_prev, good_curr)
                
                if M is not None and inliers is not None:
                    num_inliers = np.sum(inliers)
                    if num_inliers >= self.min_inliers:
                        tx = M[0, 2]
                        # Accumulate horizontal camera drift
                        self.accumulated_drift_x += tx
                        
                        # Initialize EMA tracking if not set
                        if self.low_pass_drift_x == 0.0:
                            self.low_pass_drift_x = self.accumulated_drift_x
                        else:
                            self.low_pass_drift_x = (self.ema_alpha * self.accumulated_drift_x + 
                                                     (1.0 - self.ema_alpha) * self.low_pass_drift_x)
                            
                        # High-frequency shake is the delta between absolute translation and low-pass trend
                        self.shake_x = self.accumulated_drift_x - self.low_pass_drift_x
                        
        self.prev_gray = gray

    def compensate(self, raw_x: float, frame_width: int) -> float:
        """
        Applies the current high-frequency shake correction to a raw coordinate.
        Can be called multiple times per frame (e.g., for different people).
        """
        compensated_x = raw_x - self.shake_x
        # Clamp target coordinate within frame bounds
        return max(0.0, min(float(frame_width), compensated_x))
