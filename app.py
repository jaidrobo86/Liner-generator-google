import io
import os
from flask import Flask, render_template, request, send_file, jsonify
from liner_generator import generate_liner, draw_pdf, draw_dxf, FABRIC_PRESETS

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", fabric_presets=FABRIC_PRESETS)


@app.route("/generate", methods=["POST"])
def generate():
    try:
        f   = request.form
        fmt = f.get("format", "pdf")   # "pdf" or "dxf"

        shape  = f.get("shape", "circle")
        mode   = f.get("mode", "individual")
        layout = f.get("layout", "auto")

        diam   = float(f["diameter"])        if shape == "circle"    else None
        width  = float(f["width"])           if shape == "rectangle" else None
        length = float(f["length"])          if shape == "rectangle" else None
        sd     = f.get("strip_direction", "along_width")

        fabric_ref = f.get("fabric_ref", "EL6030")
        # Custom fabric override
        rw   = float(f["roll_width"])   if f.get("roll_width")   else None
        wo   = float(f["weld_overlap"]) if f.get("weld_overlap") else None
        cgsm = float(f["gsm"])          if f.get("gsm")          else None
        cthk = float(f["thickness_mm"]) if f.get("thickness_mm") else None
        cmrl = float(f["max_roll_m"])   if f.get("max_roll_m")   else None
        cname= f.get("fabric_name", "").strip() or None

        spu  = int(f.get("strips_per_unit", 3))
        pa   = float(f.get("perimeter_allowance_mm", 0))
        full = f.get("full_coverage", "true") == "true"

        data = generate_liner(
            shape=shape,
            diameter_m=diam, width_m=width, length_m=length,
            strip_direction=sd,
            fabric_ref=fabric_ref,
            roll_width=rw, weld_overlap=wo, gsm=cgsm,
            thickness_mm=cthk, max_roll_m=cmrl, fabric_name=cname,
            layout=layout, mode=mode, strips_per_unit=spu,
            full_coverage=full, perimeter_allowance_mm=pa,
            client=f.get("client","").strip(),
            project=f.get("project","").strip(),
        )

        buf = io.BytesIO()
        if fmt == "dxf":
            draw_dxf(data, buf)
            mime     = "application/dxf"
            ext      = "dxf"
        else:
            draw_pdf(data, buf)
            mime     = "application/pdf"
            ext      = "pdf"
        buf.seek(0)

        size_label = (f"{diam}m" if shape=="circle"
                      else f"{width}x{length}m")
        filename = f"liner_{size_label}_{fabric_ref}_{mode}.{ext}"
        return send_file(buf, mimetype=mime, as_attachment=True,
                         download_name=filename)

    except (ValueError, KeyError) as e:
        return jsonify({"error": f"Input error: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Google Cloud tells your app which port to use via an environment variable
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    app.run(host="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
