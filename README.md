Skrip Python untuk secara otomatis beralih ke server DNS dengan latensi terbaik, dilengkapi dengan dashboard web yang informatif. Aplikasi ini secara berkala menguji daftar server DNS publik, menemukan yang tercepat untuk koneksi Anda, dan mengaturnya secara otomatis di sistem Anda.
## Fitur Unggulan 🚀
 * Otomatis & Cerdas: Deteksi otomatis DNS tercepat dari puluhan server DNS publik terkemuka.
 * Dashboard Web Modern: Pantau status, latensi, dan server DNS aktif secara real-time melalui antarmuka web yang responsif.
 * Grafik Latensi: Visualisasikan riwayat kinerja latensi DNS Anda melalui grafik interaktif.
 * Jeda Otomatis untuk Game: Aplikasi akan berhenti sejenak saat mendeteksi game sedang berjalan untuk mencegah gangguan koneksi.
 * Multi-Platform: Bekerja dengan baik di Windows, macOS, dan Linux.
 * Aman & Bersih: DNS akan otomatis dikembalikan ke pengaturan awal (DHCP) saat aplikasi ditutup, memastikan tidak ada perubahan permanen yang tertinggal.
## Prasyarat Sistem
 * Python 3.7+
 * Akses Administrator (Windows) atau root (macOS/Linux).
## Instalasi & Penggunaan
### 1. Siapkan Lingkungan
Pastikan Anda memiliki Python. Jika belum, unduh dari python.org dan jangan lupa centang "Add Python to PATH" saat instalasi.
### 2. Unduh Skrip
Simpan file dns.py ke folder pilihan Anda.
### 3. Instal Dependensi
Buka Command Prompt (sebagai Administrator) atau Terminal dan jalankan perintah berikut:
pip install flask psutil requests dnspython

### 4. Jalankan Skrip
 * Windows:
   * Buka Command Prompt sebagai Administrator.
   * Navigasi ke direktori tempat Anda menyimpan dns.py.
   * Jalankan perintah: python dns.py
 * macOS / Linux:
   * Buka Terminal.
   * Navigasi ke direktori tempat Anda menyimpan dns.py.
   * Jalankan perintah: sudo python3 dns.py
### 5. Akses Dashboard
Buka browser Anda dan kunjungi alamat http://127.0.0.1:8080. Biarkan skrip berjalan di latar belakang untuk pemantauan berkelanjutan.
## Konfigurasi (Opsional) ⚙️
Anda dapat menyesuaikan perilaku skrip dengan membuat file dns_config.json di folder yang sama dengan dns.py.
Contoh dns_config.json:
{
  "interval": 120,
  "threads": 15,
  "dns_query_count": 3,
  "use_ipv6": false,
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8080
  },
  "game_pause": true,
  "games": [
    "valorant.exe",
    "csgo.exe",
    "EALink.exe"
  ],
  "custom_dns": [
    "1.1.1.1",
    "8.8.8.8"
  ]
}

 * interval: Waktu (detik) antar pengujian DNS.
 * threads: Jumlah thread untuk pengujian simultan.
 * use_ipv6: Aktifkan jika jaringan Anda mendukung IPv6.
 * dashboard.host: Ubah ke "0.0.0.0" untuk mengakses dashboard dari perangkat lain di jaringan yang sama.
 * games: Tambahkan nama proses game lain untuk dideteksi.
 * custom_dns: Tambahkan server DNS kustom untuk diuji.
