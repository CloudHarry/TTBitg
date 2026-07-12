# Tambahkan bagian ini di dalam file dashboard.py kamu pada segmen PANEL POSISI:
# baris ~68 (ganti kode pembuatan pos_lines bawaan dengan ini):

        if state.has_position:
            pnl_style = "green" if (state.upnl or 0) >= 0 else "red"
            # Ambil trailing dinamis dari state jika ada
            active_trail = getattr(state, "active_trailing_pct", 1.0)
            pos_lines = [
                Text.from_markup(
                    f"[bold]POSISI:[/bold] [{'green' if state.side == 'long' else 'red'}]{state.side.upper() if state.side else '-'} "
                    f"x{state.leverage}[/] ({state.hold_duration_str()})  "
                    f"uPnL [{pnl_style}]{state.upnl:+.3f}[/{pnl_style}]"
                ),
                Text.from_markup(
                    f"E:{state.entry_price}  SL:{state.stop_loss}  TP:{state.take_profit}  sz:{state.size}"
                ),
                Text.from_markup(
                    f"Energy:{state.energy_percent()}/100  " + _bar(state.energy_percent()) + f"  [magenta]ATR-Trail: {active_trail}%[/]"
                ),
            ]