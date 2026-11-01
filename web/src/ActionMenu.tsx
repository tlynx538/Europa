import type { ComponentProps, ReactNode } from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'

type Props = { label?: string; trigger?: string; triggerClassName?: string; children: ReactNode; disabled?: boolean;
  modal?: boolean; open?: boolean; onOpenChange?: (open: boolean) => void; onCloseAutoFocus?: ComponentProps<typeof DropdownMenu.Content>['onCloseAutoFocus'] }

export function ActionMenu({ label = 'Actions', trigger = 'Actions', triggerClassName = '', children, disabled = false, modal, open, onOpenChange, onCloseAutoFocus }: Props) {
  return <DropdownMenu.Root modal={modal} open={open} onOpenChange={onOpenChange}><DropdownMenu.Trigger className={`action-trigger ${triggerClassName}`.trim()} aria-label={label} disabled={disabled}>{trigger} <span aria-hidden="true">▾</span></DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Content className="menu-content action-menu-content" align="end" sideOffset={4} collisionPadding={8} onCloseAutoFocus={onCloseAutoFocus}>{children}</DropdownMenu.Content></DropdownMenu.Portal>
  </DropdownMenu.Root>
}

export const ActionItem = DropdownMenu.Item
export const ActionSeparator = DropdownMenu.Separator
