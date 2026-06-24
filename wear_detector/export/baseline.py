# ABOUTME: Per-unit healthy baseline in embedding space: centroid + distance threshold.
# ABOUTME: The learned-feature equivalent of wear_detector's per-unit median/MAD baseline.
import numpy as np

# numpy-only public helpers; int8 embedding needs TF, imported lazily in embed_int8.


def distances(emb, centroid):
    """Euclidean distance of each embedding row to the centroid."""
    return np.linalg.norm(np.asarray(emb) - np.asarray(centroid), axis=1)


def centroid_threshold(healthy_emb, fpr=0.05):
    """Healthy centroid and the distance threshold giving ~fpr false alarms.

    centroid = mean of healthy embeddings; threshold = (1-fpr) quantile of healthy
    distances. On device this is the per-unit baseline stored after commissioning.
    """
    healthy_emb = np.asarray(healthy_emb)
    centroid = healthy_emb.mean(axis=0)
    thr = float(np.quantile(distances(healthy_emb, centroid), 1.0 - fpr))
    return centroid, thr


def dequant(int8_out, scale, zero_point):
    """int8 model output -> float embedding."""
    return (int8_out.astype(np.int32) - zero_point).astype(np.float32) * scale


def embed_int8(tflite_path, X):
    """Run the int8 encoder over X[*,49,40,1] and return float embeddings.

    Uses the pre-Vela int8 tflite (CPU reference kernels) — numerically the same int8
    arithmetic the Ethos-U55 runs, so host eval reflects on-device behaviour.
    """
    import tensorflow as tf

    interp = tf.lite.Interpreter(model_path=tflite_path)
    interp.allocate_tensors()
    inp, out = interp.get_input_details()[0], interp.get_output_details()[0]
    in_scale, in_zp = inp["quantization"]
    out_scale, out_zp = out["quantization"]
    x = np.asarray(X, np.float32).reshape((-1, X.shape[1], X.shape[2], 1))
    embs = []
    for i in range(len(x)):
        q = np.round(x[i] / in_scale + in_zp).clip(-128, 127).astype(np.int8)
        interp.set_tensor(inp["index"], q[None, ...])
        interp.invoke()
        embs.append(dequant(interp.get_tensor(out["index"])[0], out_scale, out_zp))
    return np.stack(embs)
