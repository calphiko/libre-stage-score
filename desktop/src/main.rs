#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use fs2::FileExt;
use std::{
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Mutex,
    },
    thread,
    time::Duration,
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

struct Backend {
    child: Mutex<Child>,
    _lock: File,
    shutting_down: AtomicBool,
}

impl Drop for Backend {
    fn drop(&mut self) {
        if let Ok(child) = self.child.get_mut() {
            stop_child(child);
        }
    }
}

fn stop_child(child: &mut Child) {
    drop(child.stdin.take());
    for _ in 0..50 {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(error) => {
                eprintln!("Backend-Status konnte nicht gelesen werden: {error}");
                break;
            }
        }
    }
    if let Err(error) = child.kill() {
        eprintln!("Backend konnte nicht beendet werden: {error}");
    }
    if let Err(error) = child.wait() {
        eprintln!("Backend konnte nicht abgewartet werden: {error}");
    }
}

fn start(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let data_dir = match std::env::var_os("LIBRE_STAGE_DESKTOP_DATA_DIR") {
        Some(path) if !path.is_empty() => path.into(),
        _ => app.path().app_data_dir()?,
    };
    fs::create_dir_all(&data_dir)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(data_dir.join("spoke.lock"))?;
    lock.try_lock_exclusive()
        .map_err(|_| "Die Desktop-App läuft bereits für dieses Datenverzeichnis.")?;

    let executable = std::env::current_exe()?;
    let sidecar = executable
        .parent()
        .ok_or("Das App-Verzeichnis konnte nicht ermittelt werden.")?
        .join(if cfg!(windows) {
            "spoke-backend.exe"
        } else {
            "spoke-backend"
        });
    let mut command = Command::new(sidecar);
    let log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(data_dir.join("backend.log"))?;
    command
        .arg("--data-dir")
        .arg(&data_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log_file));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or("Backend-Ausgabe nicht verfügbar.")?;
    let backend = Backend {
        child: Mutex::new(child),
        _lock: lock,
        shutting_down: AtomicBool::new(false),
    };
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) if line.starts_with("LIBRE_STAGE_READY=") => {
                    let _ = sender.send(line);
                }
                Ok(line) => eprintln!("{line}"),
                Err(error) => {
                    eprintln!("Backend-Ausgabe: {error}");
                    break;
                }
            }
        }
    });
    let line = receiver
        .recv_timeout(Duration::from_secs(120))
        .map_err(|error| {
            format!(
                "Backend konnte nicht gestartet werden: {error}. Details: {}",
                data_dir.join("backend.log").display()
            )
        })?;
    let port: u16 = line["LIBRE_STAGE_READY=".len()..].parse()?;
    if port == 0 {
        return Err("Backend meldete einen ungültigen Port.".into());
    }
    let url = format!("http://127.0.0.1:{port}/").parse()?;
    let origin = format!("http://127.0.0.1:{port}");
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("Libre Stage Score")
        .inner_size(1200.0, 800.0)
        .on_navigation(move |url| url.origin().ascii_serialization() == origin)
        .build()?;
    app.manage(backend);
    let handle = app.handle().clone();
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(2));
        let state = handle.state::<Backend>();
        if state.shutting_down.load(Ordering::Acquire) {
            break;
        }

        let result = state
            .child
            .lock()
            .expect("Backend lock poisoned")
            .try_wait();
        if state.shutting_down.load(Ordering::Acquire) {
            break;
        }

        match result {
            Ok(None) => continue,
            Ok(Some(status)) => {
                rfd::MessageDialog::new()
                    .set_title("Libre Stage Score")
                    .set_description(format!(
                        "Das lokale Backend wurde unerwartet beendet: {status}"
                    ))
                    .set_level(rfd::MessageLevel::Error)
                    .show();
            }
            Err(error) => eprintln!("Backend-Status konnte nicht gelesen werden: {error}"),
        }
        handle.exit(1);
        break;
    });
    Ok(())
}

fn main() {
    let result = tauri::Builder::default()
        .setup(|app| {
            if let Err(error) = start(app) {
                eprintln!("{error}");
                rfd::MessageDialog::new()
                    .set_title("Libre Stage Score")
                    .set_description(format!("Die App konnte nicht gestartet werden:\n{error}"))
                    .set_level(rfd::MessageLevel::Error)
                    .show();
                app.handle().exit(1);
            }
            Ok(())
        })
        .build(tauri::generate_context!());
    match result {
        Ok(app) => app.run(|handle, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(state) = handle.try_state::<Backend>() {
                    state.shutting_down.store(true, Ordering::Release);
                    if let Ok(mut child) = state.child.lock() {
                        stop_child(&mut child);
                    }
                }
            }
        }),
        Err(error) => {
            eprintln!("{error}");
            rfd::MessageDialog::new()
                .set_title("Libre Stage Score")
                .set_description(format!("Die App konnte nicht gestartet werden:\n{error}"))
                .set_level(rfd::MessageLevel::Error)
                .show();
        }
    }
}
