import type { ReactNode } from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'

export function ActionMenu({ label = 'Actions', children, disabled = false }: { label?: string; children: ReactNode; disabled?: boolean }) {
  return <DropdownMenu.Root><DropdownMenu.Trigger className="action-trigger" aria-label={label} disabled={disabled}>Actions <span aria-hidden="true">▾</span></DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Content className="menu-content action-menu-content" align="end" sideOffset={4} collisionPadding={8}>{children}</DropdownMenu.Content></DropdownMenu.Portal>
  </DropdownMenu.Root>
}

export const ActionItem = DropdownMenu.Item
export const ActionSeparator = DropdownMenu.Separator
